"""Run the read-only features against a real account, and keep the receipts.

The dry-run sweep proves a page reads its configuration; it cannot prove the page
survives Telegram. This script is the other half: a short list of features that
only read (probe the session, export the pool, watch a chat, listen for replies)
run once against a real login, with every run checked for a valid receipt.

Two rules keep it from being dangerous:

- every case names what it touches, and nothing here writes to another user's
  chat. A case that would write must also appear in ``WRITE_CASES`` and the
  script refuses to run it without ``--allow-write``;
- the two listening pages are interrupted on purpose after a few seconds, which
  is also the only way to test that Ctrl+C shuts them down cleanly.

Usage::

    PYTHONPATH=. python scripts/live_smoke.py                 # read-only cases
    PYTHONPATH=. python scripts/live_smoke.py --account t1    # a specific label
    PYTHONPATH=. python scripts/live_smoke.py --list          # what would run
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Seconds a listening page is allowed to stay up before Ctrl+C is sent.
LISTEN_SECONDS = 8


@dataclass(frozen=True)
class Case:
    name: str
    args: tuple[str, ...]
    #: Seconds to wait before interrupting, or None for "run to completion".
    interrupt_after: int | None = None
    #: The feature whose receipt this case must leave, or "" when the command is
    #: not a task (``accounts test`` only probes).
    receipt_for: str = ""
    #: Text the output must contain for the case to count as good.
    expect: str = ""
    #: This case writes into somebody's chat. Refused unless ``--allow-write``.
    write: bool = False
    #: The page is one of the CLI's write pages, so the CLI demands the flag even
    #: though this particular case cannot write (auto_reply is pointed at a rule
    #: that matches nothing). Safe by construction, not by hope.
    cli_write: bool = False
    #: Open a listener on this machine and pass it in as ``{port}``. A proxy
    #: probe needs somewhere to connect, and the only honest target is a socket
    #: this machine owns - pointing it at a stranger would be someone else's
    #: bandwidth and someone else's log.
    local_port: bool = False


CASES: tuple[Case, ...] = (
    # Scoped to the account under test: the pool may also hold a broken entry,
    # and that is a fact about the pool, not about this run. ``export`` prints
    # every row, so a dead label still shows up in the output.
    Case("probe", ("accounts", "test", "--live", "--accounts", "{account}"),
         None, "", "ok "),
    Case("export", ("export_accounts", "--live"), None, "export_accounts", ""),
    # watch_chat relays, so the CLI treats it as a write page; this case relays
    # into the account's own chat (Saved Messages) and nowhere else.
    Case("watch", ("watch_chat", "--live", "--accounts", "{account}",
                   "--targets", "{self}"), LISTEN_SECONDS, "watch_chat", "DONE:",
         cli_write=True),
    # The positive half of the write gate, on a real account: the CLI runs this
    # page only because the case carries --allow-write, and the row it writes
    # goes into the account's own chat, never to a third party.
    Case("send", ("mass_send", "--live", "--accounts", "{account}",
                  "--targets", "{self}", "--text", "ogtool smoke"),
         None, "mass_send", "DONE: sent=1", cli_write=True),
    # One heartbeat round: it changes nothing but the account's online flag, and
    # a single round is enough to prove the page can reach Telegram and wait.
    Case("online", ("keep_online", "--live", "--accounts", "{account}",
                    "--loops", "1", "--interval", "1"), None, "keep_online",
         "DONE:"),
    # One number nobody owns: the page has to reach Telegram's resolve call and
    # classify the answer, without messaging anyone.
    Case("phones", ("filter_phone", "--live", "--accounts", "{account}",
                    "--numbers", "+10000000000"), None, "filter_phone",
         "UNKNOWN"),
    Case("listen", ("auto_reply", "--live", "--accounts", "{account}",
                    "--rule", "ogtool-live-smoke-cannot-match=>smoke",
                    "--duplicate-seconds", "30"),
         LISTEN_SECONDS, "auto_reply", "DONE:", cli_write=True),
    # The dialog listing is how a real chat reference is found at all, so it is
    # worth proving that the call still works and still reports unread counts.
    Case("dialogs", ("accounts", "dialogs", "--live", "--accounts", "{account}",
                     "--limit", "5"), None, "", "total:"),
    # The speed test is the proxy page's only real action: a TCP connect and a
    # latency. A local listener gives it a genuine ok without touching anyone.
    Case("proxy", ("proxy", "--live", "--add", "socks5://127.0.0.1:{port}",
                   "--test"), None, "proxy", "ok", local_port=True),
)

#: Cases that would write to somebody's chat. None today: the list above is
#: read-only by construction, and the gate exists to keep it that way.
WRITE_CASES: frozenset[str] = frozenset()


def config_path() -> Path:
    return ROOT / "config.yaml"


def extra_path() -> Path:
    """Where the per-machine cases live (``OGTOOL_LIVE_CASES`` overrides).

    The override exists so a check can hand this script a case file of its own -
    the write gate is worth verifying without editing the file that names real
    chats.
    """
    chosen = os.environ.get("OGTOOL_LIVE_CASES", "").strip()
    return Path(chosen) if chosen else ROOT / "live_cases.yaml"


def load_extra(path: Path) -> tuple[list[Case], list[str]]:
    """Cases from a file, so covering a chat needs no code change.

    The file is per-machine (it names real chats), so a missing file is normal.
    Each entry is ``name``, ``args`` (a list), and optionally ``interrupt_after``,
    ``receipt_for``, ``expect`` and ``write: true``. A malformed entry is a
    problem, never a silent skip.
    """
    if not path.exists():
        return [], []
    import yaml

    try:
        body = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        return [], [f"{path.name}: {type(error).__name__}: {error}"]
    entries = body.get("cases") if isinstance(body, dict) else None
    if not isinstance(entries, list):
        return [], [f"{path.name}: no 'cases' list"]
    cases: list[Case] = []
    problems: list[str] = []
    for position, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or not entry.get("name") \
                or not entry.get("args"):
            problems.append(f"{path.name}: entry {position} needs name and args")
            continue
        args = entry["args"]
        if not isinstance(args, list) or not all(
                isinstance(item, str) for item in args):
            problems.append(
                f"{path.name}: {entry['name']}: args must be a list of strings "
                "(quote the placeholders: '{account}', '{self}')")
            continue
        cases.append(Case(
            name=str(entry["name"]),
            args=tuple(args),
            interrupt_after=entry.get("interrupt_after"),
            receipt_for=str(entry.get("receipt_for", "")),
            expect=str(entry.get("expect", "")),
            write=bool(entry.get("write", False)),
        ))
    return cases, problems


def receipts_dir(config: Path) -> Path:
    """Where this config's receipts land, read from the config itself."""
    text = config.read_text(encoding="utf-8")
    match = re.search(r"^logs:\s*(\S+)", text, re.MULTILINE)
    logs = (config.parent / (match.group(1) if match else "logs")).resolve()
    return logs / "receipts"


def self_username(config: Path, account: str) -> tuple[str, str]:
    """Ask Telegram who this account is; returns (username, raw ok line)."""
    run = subprocess.run(  # noqa: S603 - our own CLI, fixed arguments
        [sys.executable, "-m", "ogtool", "--config", str(config),
         "accounts", "test", "--live", "--accounts", account],
        cwd=ROOT, capture_output=True, text=True, timeout=180, check=False)
    output = run.stdout + run.stderr
    match = re.search(r"username=@(\S+)", output)
    return (match.group(1) if match else ""), output.strip()


def newest_receipt(directory: Path, feature: str) -> dict | None:
    files = sorted(directory.glob(f"{feature}-*.json"),
                   key=lambda path: path.stat().st_mtime)
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def forget_proxy_rows(config: Path, port: int) -> list[str]:
    """Remove the fixture proxy this case added.

    The live run must not leave state behind: a row pointing at a port that
    closed with this process would make the next listing a lie. Cleanup is by
    URL, not by id, because the id depends on whatever else the table holds.
    """
    sys.path.insert(0, str(ROOT))
    from ogtool.config import Settings
    from ogtool.store import Store

    settings = Settings.load(config)
    store = Store(settings.db_path)
    removed = 0
    try:
        for row in store.list_proxies():
            if f"127.0.0.1:{port}" in row.display():
                store.delete_proxy(row.id)
                removed += 1
    finally:
        store.close()
    if not removed:
        return ["proxy: the fixture row was not in the table afterwards"]
    return []


def local_listener() -> tuple[socket.socket, int]:
    """A TCP listener on this machine, closed by the caller."""
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    return server, int(server.getsockname()[1])


def run_case(case: Case, config: Path, account: str, who: str,
             allow_write: bool = False) -> list[str]:
    """One case; returns the problems it produced (empty means good)."""
    server = None
    port = 0
    if case.local_port:
        server, port = local_listener()
    args = list(case.args)
    if case.cli_write or (allow_write and (case.write or case.name in WRITE_CASES)):
        # The CLI has its own gate now, so the flag has to reach it or an
        # allowed write case would be refused at the next layer down.
        args.insert(1, "--allow-write")
    argv = [sys.executable, "-m", "ogtool", "--config", str(config), *args]
    argv = [item.format(account=account, self=who, port=port) for item in argv]
    started = subprocess.Popen(  # noqa: S603 - our own CLI
        argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True)
    problems: list[str] = []
    if case.interrupt_after is None:
        output = started.communicate(timeout=240)[0]
    else:
        try:
            output = started.communicate(timeout=case.interrupt_after)[0]
            problems.append(f"{case.name}: finished on its own, expected to be "
                            "interrupted")
        except subprocess.TimeoutExpired:
            started.send_signal(signal.SIGINT)
            try:
                output = started.communicate(timeout=60)[0]
            except subprocess.TimeoutExpired:
                started.kill()
                output = started.communicate()[0]
                problems.append(f"{case.name}: did not stop after Ctrl+C")
    if started.returncode != 0:
        problems.append(f"{case.name}: exit {started.returncode}\n"
                        f"{output[-600:]}")
    if "Traceback" in output:
        problems.append(f"{case.name}: traceback\n{output[-600:]}")
    if case.expect and case.expect not in output:
        problems.append(f"{case.name}: output lacks {case.expect!r}\n"
                        f"{output[-400:]}")
    if server is not None:
        server.close()
        problems.extend(forget_proxy_rows(config, port))
    if case.receipt_for:
        body = newest_receipt(receipts_dir(config), case.receipt_for)
        if body is None:
            problems.append(f"{case.name}: no receipt for "
                            f"{case.receipt_for}")
        else:
            sys.path.insert(0, str(ROOT))
            from ogtool import receipts as receipts_module

            problems.extend(f"{case.name}: {item}"
                            for item in receipts_module.validate(body))
    print(f"{case.name:10s} {'ok' if not problems else 'FAIL'}  "
          f"{output.strip().splitlines()[-1][:70] if output.strip() else '-'}")
    return problems


def main(argv: list[str]) -> int:
    args = argv[1:]
    cases, case_problems = load_extra(extra_path())
    if "--list" in args:
        for case in list(CASES) + cases:
            print(f"{case.name:10s} {' '.join(case.args)}")
        for problem in case_problems:
            print(problem)
        return 0 if not case_problems else 1
    account = "t1"
    if "--account" in args:
        account = args[args.index("--account") + 1]
    allow_write = "--allow-write" in args
    # ``--only NAME`` runs a single case. A check that has to prove one behaviour
    # (does the gate refuse? does the flag let it through?) should not cost the
    # whole suite's round trips to do it.
    only = ""
    if "--only" in args:
        only = args[args.index("--only") + 1]
    # Extra cases go through the same gate: a file on disk must not be able to
    # turn a write into something this script runs by default.
    # ``write`` is what the YAML says about itself; ``WRITE_CASES`` is the
    # hard-coded list of built-in ones. Both count, because a case file that
    # declares "write: true" is exactly as able to post into a chat as a case
    # written in this file - the earlier version only checked the latter, so a
    # yaml case marked write: true ran without the flag.
    refused = [case.name for case in list(CASES) + cases
               if case.write or case.name in WRITE_CASES]
    if case_problems:
        print(f"{extra_path().name} has {len(case_problems)} problem(s):")
        for problem in case_problems:
            print(f"  {problem}")
        return 2
    if refused and not allow_write:
        print(f"refusing {len(refused)} write case(s) without --allow-write: "
              f"{', '.join(refused)}")
        return 2
    config = config_path()
    if not config.exists():
        print(f"no {config}: this script needs a real login")
        return 2
    who, probe = self_username(config, account)
    if not who:
        print("the account did not identify itself; nothing else can be tested")
        print(probe)
        return 1
    print(f"account: {account} (@{who})")
    problems: list[str] = []
    every = [case for case in list(CASES) + cases
             if not only or case.name == only]
    if only and not every:
        print(f"no case named {only!r}")
        return 2
    for case in every:
        problems.extend(run_case(case, config, account, who, allow_write))
    print(f"live: {len(every)} case(s), {len(problems)} problem(s)")
    for problem in problems:
        print("-" * 60)
        print(problem)
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
