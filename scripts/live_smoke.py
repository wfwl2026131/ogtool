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
import re
import signal
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


CASES: tuple[Case, ...] = (
    # Scoped to the account under test: the pool may also hold a broken entry,
    # and that is a fact about the pool, not about this run. ``export`` prints
    # every row, so a dead label still shows up in the output.
    Case("probe", ("accounts", "test", "--live", "--accounts", "{account}"),
         None, "", "ok "),
    Case("export", ("export_accounts", "--live"), None, "export_accounts", ""),
    Case("watch", ("watch_chat", "--live", "--accounts", "{account}",
                   "--targets", "{self}"), LISTEN_SECONDS, "watch_chat", "DONE:"),
    # One heartbeat round: it changes nothing but the account's online flag, and
    # a single round is enough to prove the page can reach Telegram and wait.
    Case("online", ("keep_online", "--live", "--accounts", "{account}",
                    "--loops", "1", "--interval", "1"), None, "keep_online",
         "DONE:"),
    Case("listen", ("auto_reply", "--live", "--accounts", "{account}",
                    "--rule", "ogtool-live-smoke-cannot-match=>smoke",
                    "--duplicate-seconds", "30"),
         LISTEN_SECONDS, "auto_reply", "DONE:"),
)

#: Cases that would write to somebody's chat. None today: the list above is
#: read-only by construction, and the gate exists to keep it that way.
WRITE_CASES: frozenset[str] = frozenset()


def config_path() -> Path:
    return ROOT / "config.yaml"


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


def run_case(case: Case, config: Path, account: str, who: str) -> list[str]:
    """One case; returns the problems it produced (empty means good)."""
    argv = [sys.executable, "-m", "ogtool", "--config", str(config), *case.args]
    argv = [item.format(account=account, self=who) for item in argv]
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
    if "--list" in args:
        for case in CASES:
            print(f"{case.name:10s} {' '.join(case.args)}")
        return 0
    account = "t1"
    if "--account" in args:
        account = args[args.index("--account") + 1]
    allow_write = "--allow-write" in args
    refused = [case.name for case in CASES if case.name in WRITE_CASES]
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
    for case in CASES:
        problems.extend(run_case(case, config, account, who))
    print(f"live: {len(CASES)} case(s), {len(problems)} problem(s)")
    for problem in problems:
        print("-" * 60)
        print(problem)
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
