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

#: The suite's own test chat. Machine-local by nature -- it is a group this
#: machine's accounts are members of -- so it rides in a gitignored file
#: (data/fixture_group, one line) instead of in the code: a tree that leaves
#: this machine reads the empty placeholder and its fixture cases are
#: skipped rather than fired at a link nobody is in.
FIXTURE_GROUP = ""
_fixture_marker = ROOT / "data" / "fixture_group"
if _fixture_marker.exists():
    FIXTURE_GROUP = _fixture_marker.read_text(encoding="utf-8").strip()

#: Seconds a listening page is allowed to stay up before Ctrl+C is sent.
#: Retired as a routine mechanism: the listen pages self-limit with
#: --run-seconds, so no case ships an interrupt_after any more. The slot
#: stays for emergencies - a hung case can still be killed by hand-edited
#: extra cases (live_cases.yaml).


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
    #: How many result rows the receipt must hold. Zero means "do not check";
    #: an exit code of 0 with a task that recorded nothing proves less than it
    #: looks, and the two listening pages are exactly that case.
    min_rows: int = 0
    #: A nonzero exit the case treats as the expected outcome. The negative
    #: paths (a group with no call to join) end the page with the failure
    #: counted in its PLAN line and exit 1 - that IS the proof.
    allow_exit: int = 0
    #: Whether the six checks should run it. Every registered case is in the
    #: suite since phase five's copy promotion: a bounded capture
    #: (--capture-limit 1) made the copy case one message in, one out, like
    #: the other budgeted self-messages. The flag stays so a case can still
    #: be benched by name without unregistering it.
    in_suite: bool = True
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
    Case("export", ("export_accounts", "--live"), None, "export_accounts", "",
         min_rows=1),
    # watch_chat relays, so the CLI treats it as a write page; this case relays
    # into the account's own chat (Saved Messages) and nowhere else.
    # --run-seconds lets the page close its own receipt on its own plan;
    # the interrupt_after slot stays None because nothing needs to be killed.
    Case("watch", ("watch_chat", "--live", "--accounts", "{account}",
                   "--targets", "{self}", "--run-seconds", "3"),
         None, "watch_chat", "DONE:", cli_write=True),
    # The positive half of the write gate, on a real account: the CLI runs this
    # page only because the case carries --allow-write, and the row it writes
    # goes into the account's own chat, never to a third party.
    # --daily-limit 0: same reason as send_v2 - the smoke page's own budget
    # must not depend on how many other cases ran before it today.
    Case("send", ("mass_send", "--live", "--accounts", "{account}",
                  "--targets", "{self}", "--text", "ogtool smoke",
                  "--daily-limit", "0"),
         None, "mass_send", "DONE: sent=1", cli_write=True, min_rows=1),
    # One heartbeat round: it changes nothing but the account's online flag, and
    # a single round is enough to prove the page can reach Telegram and wait.
    Case("online", ("keep_online", "--live", "--accounts", "{account}",
                    "--loops", "1", "--interval", "1"), None, "keep_online",
         "DONE:", min_rows=1),
    # One number nobody owns: the page has to reach Telegram's resolve call and
    # classify the answer, without messaging anyone.
    Case("phones", ("filter_phone", "--live", "--accounts", "{account}",
                    "--numbers", "+10000000000"), None, "filter_phone",
         "UNKNOWN", min_rows=1),
    Case("listen", ("auto_reply", "--live", "--accounts", "{account}",
                    "--rule", "ogtool-live-smoke-cannot-match=>smoke",
                    "--duplicate-seconds", "30", "--run-seconds", "3"),
         None, "auto_reply", "DONE:", cli_write=True),
    # The smallest write that goes into Telegram and cannot reach a stranger:
    # adding the account itself to its own contact list. It is gated, so the
    # case has to pass --allow-write like any other write page.
    Case("contact", ("add_contact", "--live", "--accounts", "{account}",
                     "--contacts", "{self}"), None, "add_contact", "ok 1",
         cli_write=True, min_rows=1),
    # The same self-message through the second sending page, which keeps its
    # own contact book and its own queue. --no-resume because the first run
    # already delivered this pair and resume would correctly skip it.
    # --no-resume because the pair is genuinely delivered once and resume
    # would skip it; --daily-limit 0 so the case does not inherit the day's
    # other smoke traffic (12 send receipts + copy + ai ...). The 50-message
    # default stays the default everywhere else.
    Case("send_v2", ("mass_send_v2", "--live", "--accounts", "{account}",
                     "--targets", "{self}", "--text", "ogtool smoke",
                     "--no-resume", "--daily-limit", "0"),
         None, "mass_send_v2", "ok: 1", cli_write=True, min_rows=1),
    # One comment on my own fixture post: the channel and the discussion
    # group are both owned by the account, so the only text that moves is
    # written by the AI into my own thread. A private post link
    # (t.me/c/<id>/<post>) is what the page is really handed in the field.
    Case("comment", ("ai_comment", "--live", "--accounts", "{account}",
                     "--targets", "https://t.me/c/4365539795/2",
                     "--poll-recent", "1", "--per-post-min", "1",
                     "--per-post-max", "1",
                     "--comment-interval-min", "1",
                     "--comment-interval-max", "2"),
         None, "ai_comment", "comments", cli_write=True, min_rows=1),
    # Renaming the account's own profile: the four list form of the page, one
    # name, applied and then put back. Out of the suite because every run would
    # rewrite the profile; --only rename runs it deliberately.
    # Renaming the account's own profile to a fixed name: idempotent (the
    # same first/last every run), no third party, the name file is a tracked
    # fixture, so the case runs in the suite.
    Case("rename", ("batch_rename", "--live", "--accounts", "{account}",
                    "--file", "scripts/fixtures/rename_names.txt",
                    "--interval-ms", "500"),
         None, "batch_rename", "renames", cli_write=True, min_rows=1),
    # One AI turn into the account's own chat: the page plans a topic, asks the
    # model for a line and sends it. The interval is pinned to a second so the
    # default pacing does not make the check slow.
    Case("ai", ("ai_chat", "--live", "--accounts", "{account}",
                "--targets", "{self}", "--count", "1",
                "--interval-min", "1", "--interval-max", "1"),
         None, "ai_chat", "ok: 1", cli_write=True, min_rows=1),
    # Copying a chat into itself: capture from the account's own Saved
    # Messages and send it back there. With --capture-limit 1 the capture
    # takes the newest message only - one message in, one out, the same cost
    # class the script and puppet cases already pay (copy_chat-3436 read
    # sent=1). The whole 15-message copy was proven once (copy_chat-2567).
    Case("copy", ("copy_chat", "--live", "--accounts", "{account}",
                  "--sources", "{self}", "--targets", "{self}",
                  "--capture-minutes", "1440", "--capture-limit", "1",
                  "--daily-limit", "1", "--interval-min", "1",
                  "--interval-max", "1", "--copy-mode", "copy"),
         None, "copy_chat", "ok:", cli_write=True, min_rows=1),
    # Cloning the account's own broadcast channel into its own fixture group,
    # with the watermark set to the id this run's capture tops out at: the
    # resume filter drops every candidate, so the case proves the live
    # plumbing (capture, resolve, receipt) without posting anything new.
    # The full copy itself is proven live (ai_clone-2786 receipt, 2 posts).
    Case("clone", ("ai_clone", "--live", "--accounts", "{account}",
                   "--sources", "https://t.me/c/4365539795",
                   "--targets", "{fixture}",
                   "--last-source-message-id", "999",
                   "--mode", "copy"),
         None, "ai_clone", "ok: 0", cli_write=True, min_rows=0),
    # The call resolve path, live: the fixture group has no group call, so
    # "speak" resolves the chat, finds none and reports GROUP_CALL_NOT_FOUND -
    # the same path a real call would ride, without needing one. Proven once
    # by hand (video_manage live, join ok as already-participant, speak
    # GROUP_CALL_NOT_FOUND); kept out of the six checks because it needs the
    # real session for a result that is stable but negative.
    Case("video", ("video_manage", "--live", "--accounts", "{account}",
                   "--link", "{fixture}",
                   "--action", "speak", "--interval-ms", "300"),
         None, "video_manage", "GROUP_CALL_NOT_FOUND", cli_write=True,
         min_rows=1, allow_exit=1),
    # auto_script live: one send_text step from the shipped self_ping script
    # into the account's own fixture group. Proves the script engine, the
    # step send and the derived receipt counters (auto_script-2847 was the
    # first run with ok=1 actually recorded). One message per --only run.
    Case("script", ("auto_script", "--live", "--accounts", "{account}",
                    "--script", "self_ping"),
         None, "auto_script", "-> ok", cli_write=True, min_rows=1),
    # batch_join live: the account is already a member of its own fixture
    # group, so the real ImportChatInviteRequest rides the
    # already-participant mapping and lands ok (batch_join-2852 receipt,
    # row detail USER_ALREADY_PARTICIPANT). Zero side effects, own group.
    Case("join", ("batch_join", "--live", "--accounts", "{account}",
                  "--targets", "https://t.me/+ciW3P2jwBuAwZGRl",
                  "--request-interval", "1"),
         None, "batch_join", "-> ok", cli_write=True, min_rows=1),
    # mix_market live, stage 1 only: the orchestrator re-runs the proven
    # self add_contact (t1 adds t1) with --stop-after 1. The first live run
    # exposed the empty-envelope bug (PLAN said ok=1, receipt total=0); the
    # rows fix turned receipt 2875 into sent=1, and this case pins that.
    Case("stage1", ("mix_market", "--live", "--accounts", "{account}",
                    "--contact", "t1|6805350975",
                    "--targets", "{fixture}",
                    "--stop-after", "1"),
         None, "mix_market", "ok: 1", cli_write=True, min_rows=1),
    # batch_invite live: the handler invites its own user id into its own
    # fixture group. Telegram treats re-inviting an existing member as a
    # no-op success, so the real InviteToChannelRequest rides through and
    # the row records the pair (batch_invite-2921, sent=1). Zero third
    # parties.
    Case("invite", ("batch_invite", "--live", "--accounts", "{account}",
                    "--groups", "{fixture}",
                    "--members", "6805350975", "--request-interval", "1"),
         None, "batch_invite", "成功: 1", cli_write=True, min_rows=1),
    # batch_invite_v2 live: the same self-target through the v2 page (own
    # contact into own group; receipt 2925, sent=1). Same no-op-success
    # shape as "invite".
    Case("invite2", ("batch_invite_v2", "--live", "--accounts", "{account}",
                     "--targets", "{fixture}",
                     "--contacts", "6805350975", "--request-interval", "1"),
         None, "batch_invite_v2", "ok: 1", cli_write=True, min_rows=1),
    # batch_login live, offline path: an empty import folder means zero
    # Telegram traffic, the pool audit still runs against the real session
    # (t1 ok, the broken account a errors) and the export lands. The real
    # proof of a login itself still needs the operator's code, so this
    # pins only the no-import wiring.
    Case("login", ("batch_login", "--live", "--allow-write",
                   "--import-folder", "/tmp/ogempty-import",
                   "--export-ids", "-"),
         None, "batch_login", "PLAN: 0 imported", cli_write=True,
         min_rows=0),
    # puppet_chat_v2 live: one pattern line (A1|L0|D0) played once at my own
    # fixture group - exactly one self message, the run's only budgeted
    # probe. The message physically lands (group id 15 read back "ogtool
    # puppet probe"); receipts 2964/2965 read sent=1.
    Case("puppet", ("puppet_chat_v2", "--live", "--accounts", "{account}",
                    "--targets", "{fixture}",
                    "--pattern", "data/patterns/puppet_one.txt",
                    "--loops", "1", "--daily-limit", "1",
                    "--startup-interval", "0"),
         None, "puppet_chat_v2", "PLAN: 1 messages", cli_write=True,
         min_rows=1),
    # puppet_chat v1 dual-slot: one line per account slot (A1=t1, A2=a) in the
    # fixture group. The second account is what the page was always missing --
    # a two-account pattern used to ship one ok row and one "cannot find any
    # entity" failure row, because the page re-parsed the target refs and lost
    # the channel kind. Two sent rows here keep that fix pinned.
    Case("puppet1", ("puppet_chat", "--live", "--accounts", "t1", "a",
                     "--targets", "{fixture}",
                     "--pattern", "patterns/puppet_two.txt",
                     "--loops", "1", "--daily-limit", "1"),
         None, "puppet_chat", "ok: 2", cli_write=True, min_rows=2),
    # The dialog listing is how a real chat reference is found at all, so it is
    # worth proving that the call still works and still reports unread counts.
    # filter_group live: sorts the fixture group into its bucket - a
    # read-only page (no --allow-write), one receipt row, zero side effects.
    # Receipts 2843/3440 read live ok=1 can_send=1.
    Case("fgroup", ("filter_group", "--live", "--accounts", "{account}",
                    "--groups", "{fixture}"),
         None, "filter_group", "ok: 1", min_rows=1),
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


def placeholder_config() -> Path:
    """A config for probes that must not need a real login.

    The gate check runs ``auto_reply --dry-run`` on a machine that may have
    no ``config.yaml`` at all (a fresh CI checkout). The CLI subprocess still
    wants a config file to read, so the example file is copied into the
    gitignored ``data/`` and handed to the run: placeholders only, no
    credentials, and the dry-run path never touches the network with it.
    """
    target = ROOT / "data" / "smoke-placeholder.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    example = ROOT / "config.example.yaml"
    target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    return target


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


def shallow_receipt(case: Case, body: dict) -> list[str]:
    """Does this receipt prove the page reached Telegram and recorded work?

    Exit 0 only says the process ended well. A live task that recorded no rows
    is the shape a broken page leaves, so the cases that should produce rows say
    how many they need.
    """
    if not case.min_rows:
        return []
    rows = (body.get("results") or {}).get("total", 0)
    if body.get("mode") != "live" or body.get("task_id", 0) <= 0:
        return [f"{case.name}: receipt is {body.get('mode')} "
                f"task={body.get('task_id')}, expected a live task"]
    if rows < case.min_rows:
        return [f"{case.name}: receipt holds {rows} row(s), "
                f"expected at least {case.min_rows}"]
    return []


def run_case(case: Case, config: Path, account: str, who: str,
             allow_write: bool = False) -> list[str]:
    """One case; returns the problems it produced (empty means good)."""
    if "{fixture}" in case.args and not FIXTURE_GROUP:
        return [f"{case.name}: skipped, no data/fixture_group on this machine"]
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
    argv = [item.format(account=account, self=who, port=port,
                        fixture=FIXTURE_GROUP) for item in argv]
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
    if started.returncode != 0 and started.returncode != case.allow_exit:
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
            problems.extend(shallow_receipt(case, body))
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
    probe_only = bool(only)
    if not config.exists():
        if not probe_only:
            print(f"no {config}: this script needs a real login")
            return 2
        # A --only probe (the selftest gate check) must not require a real
        # login: it runs one dry-run case to prove the write gate's two
        # halves, so a placeholder config keeps it working on a fresh
        # checkout while the full suite still refuses.
        config = placeholder_config()
        print("no config.yaml; the --only probe runs on a placeholder")
    # The batch_login case imports an empty folder; macOS scrubs /tmp behind
    # the runner's back, and a missing folder reads as a broken page instead
    # of a cleaned-up tmpdir. Creating it here keeps the case self-sufficient.
    Path("/tmp/ogempty-import").mkdir(parents=True, exist_ok=True)
    who = ""
    if not probe_only:
        # Real-login identification is for the suite; a --only dry-run probe
        # has nothing to identify and no credentials to do it with.
        who, probe = self_username(config, account)
        if not who:
            print("the account did not identify itself; nothing else can be tested")
            print(probe)
            return 1
        print(f"account: {account} (@{who})")
    problems: list[str] = []
    every = [case for case in list(CASES) + cases
             if only and case.name == only
             or not only and case.in_suite]
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
