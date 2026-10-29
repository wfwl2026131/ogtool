"""Command line entry point: ``python -m ogtool <feature> --config x.yaml``.

``--config`` is a global option and therefore goes **before** the feature name;
a task file is passed with ``--task`` on the feature itself.  Everything a page
offers is also settable in the task YAML, and the flags are derived from the
same field list, so the two can never drift apart.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__, receipts, selftest
from .accounts import AccountError
from .config import ConfigError, Settings, load_task, merge
from .features import get, load_defaults, module, names
from .features._common import close_task, open_task, record
from .flags import section
from .loggers import task_logger
from .runtime import RunContext
from .simulate import Script
from .store import KINDS, Store
from .targets import TargetError

DEFAULT_CONFIG = "config.yaml"

# The order the console shows its entries in; P0, then P1, then P2.
ORDER = [
    "puppet_chat",
    "copy_chat",
    "ai_chat",
    "ai_comment",
    "ai_clone",
    "mass_send",
    "auto_reply",
    "watch_chat",
    "batch_join",
    "batch_login",
    "batch_rename",
    "batch_invite",
    "filter_phone",
    "filter_group",
    "video_manage",
    "proxy",
    "add_contact",
    "batch_invite_v2",
    "mass_send_v2",
    "puppet_chat_v2",
    "mix_market",
    "keep_online",
    "export_accounts",
    "auto_script",
    "settings",
]

HELP = {
    "puppet_chat": "#0 话术炒群: replay a chat_pattern script",
    "copy_chat": "#1 复制炒群: capture, filter, rewrite, replay",
    "ai_chat": "#2 AI原创炒群: generated chatter per target",
    "ai_comment": "#3 AI智能评论: comment on recent posts",
    "ai_clone": "#4 AI搬运频道: carry posts to another channel",
    "mass_send": "#5 群发消息: send to private chats, groups, clients",
    "auto_reply": "#6 自动回复: keyword rules with a duplicate cache",
    "watch_chat": "#7 监听聊天: relay what survives the exclusion list",
    "batch_join": "#8 批量加群: join groups with a pool of accounts",
    "batch_login": "#9 批量上号: import .session files and their metadata",
    "batch_rename": "#10 批量改名: rename from a .txt list",
    "batch_invite": "#11 批量拉人: invite members into target groups",
    "filter_phone": "#12 筛选手机: sort numbers into four buckets",
    "filter_group": "#13 筛选群组: sort groups into six buckets",
    "video_manage": "#14 视频管理: join or leave one group",
    "proxy": "#15 代理配置: the proxy table",
    "add_contact": "#16 批量联系人: add contacts with caps",
    "batch_invite_v2": "#17 批量拉人v2: invite your contacts into your groups",
    "puppet_chat_v2": "#19 话术炒群v2: owners play the script themselves",
    "keep_online": "#21 保持在线: keep the sessions online",
    "mass_send_v2": "#18 群发私信v2: message every account's own contacts",
    "mix_market": "#20 自动营销: the five stage marketing workflow",
    "auto_script": "#23 自动脚本: run a YAML script of primitive steps",
    "export_accounts": "#22 高级功能: export the accounts and their sessions",
    "settings": "#24 设置: the global knobs, shown and validated",
    "translate": "#T1 翻译工作台: manual + batch translation with a quota",
}

#: The reference's second surface: pages of the right toolbar (:1740461),
#: which has no console index. Order here is the reference's own toolbar
#: order (Unread Messages / Translation / Client List / QuickReply) minus
#: the pages nothing maps to yet.
TOOLBAR_ORDER = ["translate"]


def build_parser() -> argparse.ArgumentParser:
    load_defaults()
    parser = argparse.ArgumentParser(
        prog="ogtool",
        # No abbreviations: "--news" must not silently become "--news-channel".
        allow_abbrev=False,
        description="Offline toolkit for the 25 console features.",
        epilog="start with 'cp config.example.yaml config.yaml'; --config goes "
               "before the feature name, a task file with --task on the feature",
    )
    parser.add_argument("--version", action="version",
                        version=f"ogtool {__version__}")
    parser.add_argument("--config", default=DEFAULT_CONFIG,
                        help=f"global config (default: {DEFAULT_CONFIG})")
    parser.add_argument("--simulate-responses", default=None,
                        help="comma separated answers fed to the dry-run sender")

    subs = parser.add_subparsers(dest="command", required=True)
    _accounts_parser(subs)
    _runs_parser(subs)
    _logs_parser(subs)
    _selftest_parser(subs)
    _evidence_parser(subs)

    _botlinks_parser(subs)
    _todo_parser(subs)
    _pattern_parser(subs)

    for name in ORDER:
        feature = subs.add_parser(name, help=HELP.get(name, name))
        _common(feature)
        for flag in getattr(module(name), "FLAGS", ()):
            feature.add_argument(flag.name, **flag.to_kwargs())

    for name in TOOLBAR_ORDER:
        feature = subs.add_parser(name, help=HELP.get(name, name))
        _common(feature)
        for flag in getattr(module(name), "FLAGS", ()):
            feature.add_argument(flag.name, **flag.to_kwargs())

    _mass_send_parser(subs.choices["mass_send"])
    _auto_reply_parser(subs.choices["auto_reply"])
    _keep_online_parser(subs.choices["keep_online"])
    return parser


def _evidence_parser(subs: argparse._SubParsersAction) -> None:
    table = subs.add_parser("evidence",
                            help="how much proof each of the 25 features has")
    table.add_argument("--json", action="store_true",
                       help="the same rows as data")
    table.add_argument("--markdown", action="store_true",
                       help="a paste-ready markdown table")


def _evidence(settings: Settings, *, as_json: bool = False,
              as_markdown: bool = False) -> int:
    import json

    from . import evidence

    rows = evidence.collect(selftest.root(), evidence.receipts_dir(settings))
    if as_json:
        print(json.dumps(evidence.as_dict(rows), ensure_ascii=False, indent=1))
    elif as_markdown:
        print(evidence.as_markdown(rows))
    else:
        print(evidence.render(rows))
    found = evidence.problems(rows)
    for problem in found:
        print(f"  {problem}")
    return 0 if not found else 1


def _selftest_parser(subs: argparse._SubParsersAction) -> None:
    check = subs.add_parser("selftest", help="receipts + knob audit + 25 dry runs")
    check.add_argument("--only", default="",
                       help=f"comma separated subset of {', '.join(selftest.CHECKS)}")
    check.add_argument("--timeout", type=float, default=900.0,
                       help="seconds before the sweep is given up on")


def _selftest(args: argparse.Namespace, settings: Settings) -> int:
    wanted = [part.strip() for part in args.only.split(",") if part.strip()]
    unknown = [name for name in wanted if name not in selftest.CHECKS]
    if unknown:
        print(f"ERROR: unknown check {', '.join(unknown)}; "
              f"pick from {', '.join(selftest.CHECKS)}")
        return 2
    return selftest.run(settings, only=wanted, timeout=args.timeout)


def _logs_parser(subs: argparse._SubParsersAction) -> None:
    logs = subs.add_parser(
        "logs", help="run transcripts: what a run printed, kept on disk")
    logs.add_argument("action", nargs="?", default="list",
                      choices=["list", "tail"],
                      help="list the transcripts, or tail the newest")
    logs.add_argument("-n", type=int, default=50,
                      help="tail: how many lines (default 50)")
    logs.add_argument("--file", default=None,
                      help="tail a named transcript instead of the newest")
    logs.add_argument("--follow", action="store_true",
                      help="tail: keep streaming until Ctrl+C")


def _runs_parser(subs: argparse._SubParsersAction) -> None:
    runs = subs.add_parser("runs", help="what the last runs left behind")
    run_subs = runs.add_subparsers(dest="action", required=True)
    listing = run_subs.add_parser("list", help="receipts, newest first")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--feature", default="", help="one page only")
    listing.add_argument("--json", action="store_true")
    show = run_subs.add_parser("show", help="one receipt in full, as JSON")
    show.add_argument("--feature", default="", help="page name (optional)")
    show.add_argument("--id", type=int, default=0, help="task id")
    diff = run_subs.add_parser("diff", help="what changed between two runs")
    diff.add_argument("--feature", required=True)
    diff.add_argument("--from-id", type=int, default=0, help="older task id")
    diff.add_argument("--to-id", type=int, default=0, help="newer task id")
    check = run_subs.add_parser("check", help="validate every receipt")
    check.add_argument("--feature", default="")
    prune = run_subs.add_parser("prune",
                                help="drop receipts beyond the newest N per page")
    prune.add_argument("--keep", type=int, default=20,
                       help="how many to keep per feature (default 20)")
    prune.add_argument("--feature", default="", help="one page only")
    prune.add_argument("--yes", action="store_true",
                       help="actually delete; without it only the plan is printed")


def _logs(args: argparse.Namespace, settings: Settings) -> int:
    """Run transcripts: list them, or tail the newest/named one."""
    from . import runlog

    if args.action == "list":
        directory = runlog.runs_dir(settings.log_dir)
        files = sorted(directory.glob("*.log"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            print("no run transcripts yet - every command writes one")
            return 0
        for path in files[:15]:
            age = time.time() - path.stat().st_mtime
            fresh = " ●" if age < 120 else ""
            stamp = datetime.fromtimestamp(path.stat().st_mtime)
            print(f"{path.name}  {stamp:%m-%d %H:%M}  "
                  f"{sum(1 for _ in path.open(encoding='utf-8'))} lines"
                  f"{fresh}")
        return 0
    # tail
    path = (Path(args.file) if args.file
            else runlog.newest(settings.log_dir))
    if path is None or not path.is_file():
        print("no run transcripts yet")
        return 0
    for line in runlog.read_tail(path, args.n):
        print(line)
    if args.follow:
        print(f"--- following {path.name} (Ctrl+C stops) ---")
        try:
            runlog.follow(path)
        except KeyboardInterrupt:  # pragma: no cover - interactive
            print("(stopped)")
    return 0


def _runs(args: argparse.Namespace, settings: Settings) -> int:
    """Read the receipts back: the run's own account of itself."""
    import json

    directory = receipts.receipt_dir(settings)
    bodies, problems = receipts.read_all(directory)
    if args.action == "check":
        for body in bodies:
            problems.extend(f"{body.get('feature', '?')}: {problem}"
                            for problem in receipts.validate(body))
        print(f"receipts: {len(bodies)}  problems: {len(problems)}")
        for problem in problems:
            print(f"  {problem}")
        return 0 if not problems else 1
    if args.action == "prune":
        if args.keep < 1:
            print("ERROR: --keep must be at least 1")
            return 2
        doomed = receipts.prune(directory, args.keep, args.feature)
        verb = "deleted" if args.yes else "would delete"
        for path in doomed:
            if args.yes:
                path.unlink(missing_ok=True)
            print(f"{verb} {path.name}")
        remaining = sorted(directory.glob("*.json"))
        print(f"prune: {verb} {len(doomed)} receipt(s), "
              f"{len(remaining)} {('remain' if args.yes else 'would remain')} "
              f"(keep {args.keep} per feature)")
        if not args.yes:
            print("dry-run: pass --yes to delete")
        return 0

    if args.action == "diff":
        return _runs_diff(args, bodies)
    if args.action == "show":
        wanted = [body for body in bodies
                  if (not args.feature or body.get("feature") == args.feature)
                  and (not args.id or body.get("task_id") == args.id)]
        if not wanted:
            print("ERROR: no receipt matches")
            return 2
        print(json.dumps(sorted(wanted, key=receipts.order)[-1],
                         ensure_ascii=False, indent=2))
        return 0
    rows = sorted((body for body in bodies
                   if not args.feature or body.get("feature") == args.feature),
                  key=receipts.order, reverse=True)[: max(0, args.limit)]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print(f"no receipts in {directory}")
        return 0
    for body in rows:
        print(receipts.line(body))
    return 0


def _runs_diff(args: argparse.Namespace, bodies: list[dict]) -> int:
    """Two receipts of one page, and the lines that differ."""
    rows = sorted((body for body in bodies
                   if body.get("feature") == args.feature),
                  key=receipts.order)
    if args.from_id:
        rows = [body for body in rows if body.get("task_id") == args.from_id] \
            or rows
    if args.to_id:
        rows = [body for body in rows if body.get("task_id") == args.to_id] \
            or rows
    if len(rows) < 2:
        print(f"ERROR: need two receipts for {args.feature}, "
              f"found {len(rows)}")
        return 2
    before, after = rows[-2], rows[-1]
    changes = receipts.diff(before, after)
    print(f"{args.feature}: task {before.get('task_id')} -> "
          f"{after.get('task_id')}")
    if not changes:
        print("no difference")
        return 0
    width = max(len(key) for key, _old, _new in changes)
    for key, old, new in changes:
        print(f"  {key:{width}s}  {old!r} -> {new!r}")
    return 0


def _accounts_parser(subs: argparse._SubParsersAction) -> None:
    accounts = subs.add_parser("accounts", help="account pool")
    account_subs = accounts.add_subparsers(dest="action", required=True)

    add = account_subs.add_parser("add", help="add a phone number or a session")
    add.add_argument("--phone", default="",
                     help="phone number, e.g. +8613800000000")
    add.add_argument("--session", default="", help="path to an existing .session")
    add.add_argument("--label", default="", help="name used in every other command")
    add.add_argument("--notes", default="")
    add.add_argument("--dry-run", action="store_true",
                     help="write the pool entry only (the default)")
    add.add_argument("--live", action="store_true",
                     help="talk to Telegram (default: dry-run only)")

    listing = account_subs.add_parser("list", help="list the pool")
    listing.add_argument("--json", action="store_true")

    kind = account_subs.add_parser(
        "kind", help="record frozen / one_way / two_way for a number")
    kind.add_argument("--label", default="", help="pool label")
    kind.add_argument("--kind", default="",
                      help="frozen|one_way|two_way, or empty to clear it")

    test = account_subs.add_parser("test", help="connectivity test")
    test.add_argument("--accounts", nargs="*", default=None)
    test.add_argument("--dry-run", action="store_true", help="the default")
    test.add_argument("--live", action="store_true")

    dialogs = account_subs.add_parser(
        "dialogs", help="list the chats an account can see (read only)")
    dialogs.add_argument("--accounts", nargs="*", default=None)
    dialogs.add_argument("--limit", type=int, default=20)
    dialogs.add_argument("--kind", default="",
                         help="only group / channel / user / bot / chat")
    dialogs.add_argument("--dry-run", action="store_true", help="the default")
    dialogs.add_argument("--live", action="store_true")

    spambot = account_subs.add_parser(
        "spambot", help="ask @spambot whether an account is limited (frozen)")
    spambot.add_argument("--accounts", nargs="*", default=None)
    spambot.add_argument("--wait", type=float, default=8.0,
                         help="seconds to wait for the bot's answer")
    spambot.add_argument("--apply", action="store_true",
                         help="write the verdict into the pool's kind column")
    logout = account_subs.add_parser(
        "logout", help="batch logout: move the session files to a trash folder")
    logout.add_argument("--accounts", nargs="*", default=None)
    logout.add_argument("--trash", default=None,
                        help="where the session files go (default: sessions/trash)")
    logout.add_argument("--dry-run", action="store_true", help="the default")
    logout.add_argument("--live", action="store_true",
                        help="really move the session files (default: dry-run)")
    password = account_subs.add_parser(
        "password",
        help="batch modify cloud passwords for the selected accounts")
    password.add_argument("--accounts", nargs="*", default=None)
    password.add_argument("--new-password-file", default=None,
                          help="file holding the new password (never a flag)")
    password.add_argument("--current-password-file", default=None,
                          help="file holding the current password, when there is one")
    password.add_argument("--dry-run", action="store_true", help="the default")
    password.add_argument("--live", action="store_true",
                          help="really change the password (default: dry-run)")
    spambot.add_argument("--dry-run", action="store_true", help="the default")
    spambot.add_argument("--live", action="store_true",
                         help="really talk to @spambot (default: dry-run)")

    login = account_subs.add_parser(
        "login", help="live login, the code read from a file (no prompts)")
    login.add_argument("--label", default="", help="pool label for the account")
    login.add_argument("--phone", default="", help="phone number to sign in")
    login.add_argument("--code-file", default="",
                       help="file the harness drops the login code into")
    login.add_argument("--password-file", default="",
                       help="file the harness drops the 2FA password into")
    login.add_argument("--timeout", type=float, default=300.0,
                       help="seconds to wait for each file")
    login.add_argument("--dry-run", action="store_true",
                       help="print the plan, connect to nothing")
    login.add_argument("--live", action="store_true", help="the default here")


def _pattern_parser(subs: argparse._SubParsersAction) -> None:
    pattern = subs.add_parser(
        "pattern", help="collect a chat_pattern script from a real chat")
    pattern_subs = pattern.add_subparsers(dest="action", required=True)
    collect = pattern_subs.add_parser("collect")
    collect.add_argument("--target", required=True,
                         help="group/channel to learn the script from")
    collect.add_argument("--account", default="",
                         help="account that reads the history")
    collect.add_argument("--hours", type=float, default=24.0,
                         help="how far back to walk (default 24)")
    collect.add_argument("--cap", type=int, default=10000,
                         help="the collector's own message ceiling")
    collect.add_argument("--out", default="",
                         help="script path (default tdata/chat_pattern/<name>.txt)")
    collect.add_argument("--delay-min", type=float, default=5.0)
    collect.add_argument("--delay-max", type=float, default=15.0)
    collect.add_argument("--slot", type=int, default=0,
                         help="account slot written into every line")
    collect.add_argument("--reply-level", type=int, default=0)
    collect.add_argument("--dry-run", action="store_true", help="the default")
    collect.add_argument("--live", action="store_true",
                         help="really read the history (default: dry-run)")


def _todo_parser(subs: argparse._SubParsersAction) -> None:
    todo = subs.add_parser("todo", help="the customer-service queue "
                                        "(from_todo_list)")
    todo_subs = todo.add_subparsers(dest="action", required=True)

    listing = todo_subs.add_parser("list", help="the queue, newest first")
    listing.add_argument("--status", default="pending",
                         choices=["pending", "done", "archived", "all"])
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--include-blacklisted", action="store_true")
    listing.add_argument("--show-service", action="store_true",
                         help="keep join/leave notifications (Filter service messages off)")
    listing.add_argument("--show-bot", action="store_true",
                         help="keep bot replies (Filter bot messages off)")
    listing.add_argument("--menu-links", default="",
                         help="promo links for the reply menu, comma separated")
    listing.add_argument("--links-per-menu", type=int, default=3)
    listing.add_argument("--no-clean-previous", action="store_true",
                         help="keep the menu on older messages too")
    listing.add_argument("--json", action="store_true")

    add = todo_subs.add_parser("add", help="queue one incoming message")
    add.add_argument("--from-id", required=True)
    add.add_argument("--peer-id", required=True)
    add.add_argument("--from-name", default="")
    add.add_argument("--peer-name", default="")
    add.add_argument("--receiver-id", default="0")
    add.add_argument("--msg", default="")
    add.add_argument("--media", default="")
    add.add_argument("--priority", type=int, default=1)
    add.add_argument("--service", action="store_true")
    add.add_argument("--bot", action="store_true")
    add.add_argument("--blacklisted", action="store_true")

    mark = todo_subs.add_parser("mark", help="mark rows read or unread")
    mark.add_argument("ids", nargs="+", type=int)
    mark.add_argument("--unread", action="store_true")

    block = todo_subs.add_parser("blacklist", help="blacklist or unblacklist rows")
    block.add_argument("ids", nargs="+", type=int)
    block.add_argument("--off", action="store_true")

    archive = todo_subs.add_parser("archive", help="move rows out of the queue")
    archive.add_argument("ids", nargs="*", type=int,
                         help="empty means every done/archived row")
    archive.add_argument("--dry-run", action="store_true", help="the default")
    archive.add_argument("--live", action="store_true",
                         help="really move the rows (default: dry-run)")


def _botlinks_parser(subs: argparse._SubParsersAction) -> None:
    from .botlinks import FLAGS

    parser = subs.add_parser(
        "bot_links",
        help="batch open bot links with the console's own start/startapp rules")
    _common(parser)
    for flag in FLAGS:
        parser.add_argument(flag.name, **flag.to_kwargs())


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", default=None, help="task YAML merged over config")
    parser.add_argument("--accounts", nargs="*", default=None,
                        help="account labels (default: the whole pool)")
    parser.add_argument("--task-name", default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="plan only, send nothing (the default)")
    parser.add_argument("--live", action="store_true",
                        help="really talk to Telegram (default: dry-run)")
    parser.add_argument("--allow-write", action="store_true",
                        help="needed for a page that writes into Telegram")
    parser.add_argument("--verbose", action="store_true")


def _mass_send_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--targets", default=None, help="comma separated entries")
    parser.add_argument("--targets-file", action="append", default=[],
                        help="file with one entry per line (repeatable)")
    parser.add_argument("--target-kind", default="custom",
                        choices=["private", "group", "channel", "client", "custom"])
    parser.add_argument("--text", default=None)
    parser.add_argument("--media", default=None)
    parser.add_argument("--forward-from", default=None)
    parser.add_argument("--interval-min", type=float, default=None)
    parser.add_argument("--interval-max", type=float, default=None)
    parser.add_argument("--group-interval", type=float, default=None)
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--daily-limit", type=int, default=None)
    parser.add_argument("--success-cap", type=int, default=None)
    parser.add_argument("--exec-cap", type=int, default=None)
    parser.add_argument("--loop-rounds", type=int, default=None,
                        help="send this many rounds (0 = until interrupted)")
    parser.add_argument("--loop-pause", type=float, default=None,
                        help="seconds between rounds")


def _auto_reply_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--rule", action="append", default=[],
                        help="keyword=>reply (repeatable)")
    parser.add_argument("--duplicate-seconds", type=int, default=None)
    parser.add_argument("--run-seconds", type=int, default=None,
                        help="stop listening after this many seconds (0: "
                             "until interrupted)")
    parser.add_argument("--simulate", action="append", default=[],
                        help="peer|sender|text (repeatable)")
    parser.add_argument("--disabled", action="store_true")


def _keep_online_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--interval", type=float, default=None,
                        help="seconds between heartbeats")
    parser.add_argument("--loops", type=int, default=None)


def _overrides(args: argparse.Namespace) -> dict[str, Any]:
    """Turn the CLI flags into the same shape a task YAML has."""
    raw: dict[str, Any] = {}
    if getattr(args, "accounts", None) is not None:
        raw["accounts"] = args.accounts
    if getattr(args, "task_name", ""):
        raw["task_name"] = args.task_name

    if args.command == "mass_send":
        return raw | _mass_send_overrides(args)
    if args.command == "auto_reply":
        return raw | _auto_reply_overrides(args)
    if args.command == "pattern":
        # Page-less: the collector takes its knobs from the parser, not from a
        # task section, so there is nothing to merge.
        return raw
    if args.command == "bot_links":
        from .botlinks import FLAGS, SECTION

        values = section(args, FLAGS)
        return raw | ({SECTION: values} if values else {})
    if args.command == "keep_online":
        online: dict[str, Any] = {}
        if args.interval is not None:
            online["interval"] = args.interval
        if args.loops is not None:
            online["loops"] = args.loops
        return raw | ({"keep_online": online} if online else {})

    holder = module(args.command)
    values = section(args, tuple(getattr(holder, "FLAGS", ())))
    if values:
        raw[str(getattr(holder, "SECTION", args.command))] = values
    return raw


def _mass_send_overrides(args: argparse.Namespace) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    targets: dict[str, Any] = {"kind": args.target_kind}
    if args.targets:
        targets["inline"] = args.targets
    if args.targets_file:
        targets["files"] = args.targets_file
    if targets.keys() - {"kind"}:
        raw["targets"] = targets
    content: dict[str, Any] = {}
    if args.text is not None:
        content["text"] = args.text
    if args.media:
        content["media"] = args.media
    if args.forward_from:
        content["forward_from"] = args.forward_from
    if content:
        raw["content"] = content
    send: dict[str, Any] = {}
    for key, value in (
        ("account_interval_min", args.interval_min),
        ("account_interval_max", args.interval_max),
        ("group_interval", args.group_interval),
        ("threads", args.threads),
        ("daily_limit", args.daily_limit),
        ("success_cap", args.success_cap),
        ("exec_cap", args.exec_cap),
        ("loop_rounds", args.loop_rounds),
        ("loop_pause", args.loop_pause),
    ):
        if value is not None:
            send[key] = value
    if send:
        raw["send"] = send
    return raw


def _auto_reply_overrides(args: argparse.Namespace) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    section_values: dict[str, Any] = {}
    if args.rule:
        section_values["rules"] = list(args.rule)
    if args.duplicate_seconds is not None:
        section_values["duplicate_seconds"] = args.duplicate_seconds
    if getattr(args, "run_seconds", None) is not None:
        section_values["run_seconds"] = args.run_seconds
    if args.disabled:
        section_values["enabled"] = False
    if section_values:
        raw["auto_reply"] = section_values
    if args.simulate:
        raw["simulate"] = list(args.simulate)
    return raw


def _settings(path: str) -> Settings:
    target = Path(path).expanduser()
    if not target.exists():
        raise ConfigError(
            f"{target} not found; run 'cp config.example.yaml config.yaml' "
            "first (--config goes before the feature name)"
        )
    return Settings.load(target)


def run(argv: list[str] | None = None) -> int:
    load_defaults()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = _settings(args.config)
    except ConfigError as error:
        print(f"ERROR: {error}")
        return 2

    if getattr(args, "dry_run", False) and getattr(args, "live", False):
        print("ERROR: --dry-run and --live are mutually exclusive")
        return 2

    refusal = _write_refusal(args)
    if refusal:
        print(refusal)
        return 2

    if args.command == "accounts":
        return _accounts(args, settings)
    if args.command == "runs":
        return _runs(args, settings)
    if args.command == "logs":
        return _logs(args, settings)

    # Every other command prints its transcript twice: to the terminal as it
    # happens, and to logs/runs/<command>-<ts>.log for later or live tailing.
    from . import runlog

    with runlog.start(settings, args.command) as _transcript:
        return _dispatch(args, settings)


def _dispatch(args: argparse.Namespace, settings: object) -> int:
    if args.command == "selftest":
        return _selftest(args, settings)
    if args.command == "evidence":
        return _evidence(settings, as_json=getattr(args, "json", False),
                         as_markdown=getattr(args, "markdown", False))
    if args.command == "todo":
        return _todo(args, settings)
    base = settings.raw
    if getattr(args, "task", None):
        try:
            base = load_task(args.task, base)
        except ConfigError as error:
            print(f"ERROR: {error}")
            return 2
    raw = merge(base, _overrides(args))
    if getattr(args, "simulate_responses", None):
        raw["simulate_responses"] = [
            part.strip() for part in args.simulate_responses.split(",")
            if part.strip()
        ]

    logger = task_logger(settings.log_dir, raw.get("task_name")
                         or args.command,
                         level=10 if getattr(args, "verbose", False) else 20)
    store = Store(settings.db_path)
    ctx = RunContext(settings=settings, store=store, log=logger,
                     live=bool(args.live),
                     script=Script.parse(raw.get("simulate_responses")))
    if args.command == "pattern":
        try:
            return _pattern(args, ctx)
        finally:
            ctx.close()
            store.close()

    if args.command == "bot_links":
        from .botlinks import BotLinkConfig, BotLinkError
        from .botlinks import run as run_bot_links

        try:
            config = BotLinkConfig.from_dict(raw, settings, live=ctx.live)
        except BotLinkError as error:
            print(f"ERROR: {error}")
            ctx.close()
            store.close()
            return 2
        try:
            return run_bot_links(config, ctx)
        finally:
            ctx.close()
            store.close()

    # Every run must leave evidence: a page that closes its task through
    # close_task writes a receipt itself, so all we need here is to know whether
    # the directory grew. Comparing the set catches the pages that write their
    # task by hand (keep_online, auto_reply, mass_send) as well.
    before = receipts.snapshot(settings)
    code = 0
    try:
        code = get(args.command)(raw, ctx)
        return code
    except ConfigError as error:
        print(f"ERROR: {error}")
        code = 2
        return 2
    except (AccountError, TargetError) as error:
        print(f"ERROR: {error}")
        code = 2
        return 2
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print("interrupted")
        code = 130
        return 130
    finally:
        # Five pages (keep_online, auto_reply, mass_send, proxy, settings) never
        # open a task, so close_task cannot speak for them. Without this a run
        # would leave no evidence behind at all - and a sweep that only sees 20
        # receipts cannot claim all 25 ran.
        if receipts.snapshot(settings) == before:
            receipts.write_run(ctx.settings, args.command, exit_code=code,
                               live=ctx.live)
        ctx.close()
        store.close()


#: ``accounts`` actions that change something on Telegram rather than reading it.
WRITE_ACCOUNT_ACTIONS: frozenset[str] = frozenset({"logout", "password",
                                                   "spambot"})

#: Commands outside the feature registry that still write: ``bot_links`` opens
#: bot links with the account, which starts whatever that bot does.
WRITE_COMMANDS: frozenset[str] = frozenset({"bot_links"})


def _write_refusal(args: argparse.Namespace) -> str:
    """The sentence a write page prints instead of running, or "".

    A dry run never reaches Telegram, so it is never refused - the gate is about
    what actually leaves this machine. Nothing is opened before this point: no
    task, no receipt, no client.
    """
    from .features import WRITE_FEATURES

    if not getattr(args, "live", False) or getattr(args, "allow_write", False):
        return ""
    command = getattr(args, "command", "")
    if command == "accounts":
        action = getattr(args, "action", "")
        if action in WRITE_ACCOUNT_ACTIONS:
            return (f"ERROR: accounts {action} writes into Telegram; add "
                    f"--allow-write to run it live")
        return ""
    if command in WRITE_FEATURES or command in WRITE_COMMANDS:
        return (f"ERROR: {command} writes into Telegram; add --allow-write to "
                f"run it, or drop --live for a plan only")
    return ""


def _pattern(args: argparse.Namespace, ctx: RunContext) -> int:
    """``pattern collect``: learn a replayable script from a real chat."""
    from .chat_pattern import PATTERN_DIR, PatternError
    from .pattern import COLLECT_CAP, CollectConfig, collect, to_lines, write_script

    cap = max(1, min(args.cap or COLLECT_CAP, COLLECT_CAP))
    out = args.out or str(PATTERN_DIR / f"collected_{args.slot}.txt")
    config = CollectConfig(target=args.target, out_path=out,
                           account=args.account, hours=args.hours, cap=cap,
                           delay_min=args.delay_min,
                           delay_max=args.delay_max, slot=args.slot,
                           reply_level=args.reply_level)
    print(f"collecting from {config.target} for {config.hours:g} hour(s), "
          f"cap {config.cap}")
    if not ctx.live:
        print(f"dry-run: would read up to {config.cap} message(s) and write "
              f"{out}")
        return 0
    try:
        result = collect(ctx.sender(), config, ctx)
    except PatternError as error:
        print(f"ERROR: {error}")
        return 2
    lines, dropped = to_lines(config, result.messages)
    if not lines:
        print(result.stopped)
        print("nothing to write: the walk returned no usable message")
        return 1
    result.written = write_script(
        Path(out), lines,
        header=f"collected from {config.target} ({len(lines)} line(s))")
    print(result.stopped)
    if dropped:
        print(f"skipped {dropped} message(s) the format cannot express")
    print(f"script: {result.written} ({len(lines)} line(s))")
    return 0


def _todo(args: argparse.Namespace, settings: Settings) -> int:
    """The queue commands. Mirrors the page's own verbs: mark, block, archive."""
    import json

    from .store import Store as _Store
    from .todo import (
        DONE,
        PENDING,
        STATUS_NAMES,
        MenuConfig,
        TodoFilter,
        TodoRecord,
        render,
    )

    store = _Store(settings.db_path)
    try:
        if args.action == "add":
            row_id = store.upsert_todo({
                "from_id": args.from_id, "from_name": args.from_name,
                "peer_id": args.peer_id, "peer_name": args.peer_name,
                "receiver_id": args.receiver_id, "msg": args.msg,
                "media": args.media, "priority": args.priority,
                "is_service": args.service, "is_bot": args.bot,
                "is_blacklist": args.blacklisted,
            })
            print(f"todo #{row_id} added")
            return 0
        if args.action == "mark":
            status = PENDING if args.unread else DONE
            changed = store.set_todo_status(args.ids, status)
            print(f"marked {changed} row(s) {STATUS_NAMES[status]}")
            return 0 if changed else 1
        if args.action == "blacklist":
            changed = store.set_todo_blacklist(args.ids, not args.off)
            print(f"blacklist {'off' if args.off else 'on'}: {changed} row(s)")
            return 0 if changed else 1
        if args.action == "archive":
            if not args.live:
                if args.ids:
                    print(f"dry-run: would archive {len(args.ids)} row(s)")
                else:
                    print("dry-run: would archive every done/archived row")
                return 0
            moved = store.archive_todos(args.ids or None)
            print(f"archived {moved} row(s)")
            return 0 if moved else 1
        status = {"pending": PENDING, "done": DONE, "archived": 3,
                  "all": None}[args.status]
        rows = store.list_todos(status, limit=args.limit)
        filter_ = TodoFilter(hide_service=not args.show_service,
                            hide_bot=not args.show_bot,
                            include_blacklisted=args.include_blacklisted)
        menu = MenuConfig(links=[part.strip() for part in
                                 args.menu_links.split(",") if part.strip()],
                          links_per_menu=args.links_per_menu,
                          auto_clean_previous=not args.no_clean_previous)
        records = [TodoRecord(**{key: row[key] for key in (
            "from_id", "peer_id", "receiver_id", "from_name", "peer_name",
            "chat_type", "msg_type", "msg_id", "title", "msg", "media",
            "priority", "status", "is_blacklist", "is_service", "is_bot",
            "unread_count", "from_count", "last_msg_at", "created_at",
            "updated_at", "id")}) for row in rows]
        records = [record for record in records
                   if record.status == status or status is None]
        if args.json:
            print(json.dumps([record.__dict__ for record in records],
                             ensure_ascii=False, default=str))
            return 0
        lines = render(records, filter_, menu if menu.links else None,
                       now=int(__import__("time").time()))
        if not lines:
            print(f"queue empty (status={args.status})")
            return 0
        for line in lines:
            print(line)
        hidden = len(records) - sum(1 for record in records
                                    if filter_.keeps(record))
        print(f"total: {len(records)} kept: {len(records) - hidden} "
              f"filtered: {hidden}")
        return 0
    finally:
        store.close()


def _read_secret(path: str | None) -> str:
    """Read a password from a file: a flag would land in the shell history."""
    if not path:
        return ""
    from pathlib import Path as _Path

    return _Path(path).expanduser().read_text(encoding="utf-8").strip()



def _account_rows_task(settings: Settings, store: Store, action: str,
                       rows: list[tuple[str, bool, str]],
                       live: bool) -> None:
    """Record one row per account for an accounts action, then close.

    These actions (test / logout / password / spambot) are all the same shape: a
    list of ``(label, ok, detail)`` printed to the screen and, until now, nothing
    on disk but the CLI's fallback receipt. One row each is what makes the counts
    checkable.
    """
    ctx = RunContext(settings=settings, store=store,
                     log=task_logger(settings.log_dir, f"accounts-{action}"),
                     live=live)
    task = open_task(ctx, action, "accounts", {"live": live},
                     planned=len(rows))
    for label, ok, detail in rows:
        record(ctx, task, label, label, ok,
               "" if ok else str(detail).split(":")[0].split(" ")[0],
               detail)
    close_task(ctx, task, derive=True)
    ctx.close()

def _accounts(args: argparse.Namespace, settings: Settings) -> int:
    store = Store(settings.db_path)
    pool = _Pool(settings, store)
    try:
        if args.action == "login":
            from .login import LoginConfig, login

            return login(LoginConfig(
                label=args.label,
                phone=args.phone,
                code_file=args.code_file,
                password_file=args.password_file,
                timeout=args.timeout,
                dry_run=bool(args.dry_run),
            ), settings, store)
        if args.action == "add":
            if args.session:
                account = pool.add_session(args.session, args.label)
            elif args.phone:
                account = pool.add_phone(args.phone, args.label, args.notes)
            else:
                print("ERROR: give --phone or --session")
                return 2
            print(f"added {account.label} session={account.session} "
                  f"status={account.status}")
            return 0
        if args.action == "kind":
            if not args.label:
                print("ERROR: --kind needs --label")
                return 2
            wanted = args.kind.strip()
            if wanted not in KINDS:
                print(f"ERROR: --kind wants {'|'.join(KINDS)} or empty, "
                      f"not {args.kind!r}")
                return 2
            if args.label not in {row.label for row in pool.list()}:
                print(f"ERROR: no such account: {args.label}")
                return 2
            store.set_account_kind(args.label, wanted)
            print(f"{args.label} kind={wanted or '-'}")
            return 0
        if args.action == "logout":
            rows = pool.logout(args.accounts, live=bool(args.live),
                               trash=args.trash or "")
            for label, ok, detail in rows:
                print(f"{'ok  ' if ok else 'fail'} {label} {detail}")
            done = sum(1 for _, ok, _ in rows if ok)
            print(f"Batch logout finished. Success: {done}/{len(rows)}")
            _account_rows_task(settings, store, "logout", rows,
                               live=bool(args.live))
            return 0 if done == len(rows) else 1
        if args.action == "password":

            try:
                new_password = _read_secret(args.new_password_file)
                current_password = _read_secret(args.current_password_file)
            except OSError as error:
                print(f"ERROR: {error}")
                return 2
            rows = pool.passwords(args.accounts, new_password,
                                  live=bool(args.live),
                                  current_password=current_password)
            for label, ok, detail in rows:
                print(f"{'ok  ' if ok else 'fail'} {label} {detail}")
            done = sum(1 for _, ok, _ in rows if ok)
            print(f"Password change task finished. "
                  f"Success: {done}/{len(rows)}")
            _account_rows_task(settings, store, "password", rows,
                               live=bool(args.live))
            return 0 if done == len(rows) else 1
        if args.action == "dialogs":
            # Listing is the only read here, and the list itself is the result:
            # one row per dialog, so the receipt names what the account could see
            # instead of only that the command exited. That matters because this
            # listing is how the live checks find a real chat to point at.
            ctx = RunContext(settings=settings, store=store,
                             log=task_logger(settings.log_dir, "accounts-dialogs"),
                             live=bool(args.live))
            task = open_task(ctx, "dialogs", "accounts",
                             {"limit": args.limit, "kind": args.kind}, planned=0)
            try:
                rows = pool.dialogs(args.accounts, live=bool(args.live),
                                    limit=args.limit)
            finally:
                ctx.close()
            wanted = (args.kind or "").strip().lower()
            shown = [row for row in rows if not wanted or row[1] == wanted]
            for label, kind, ref, title in shown:
                name, _, rest = title.partition("\t")
                print(f"{kind:8s} {ref:20s} {name}  ({label}) {rest}")
                if not args.live:
                    continue
                # A row with no ref is the account failing, not a dialog: the
                # error name is in the detail because that is where the pool put
                # it. A dialog with no username still has an id, which is a ref.
                ok = bool(ref)
                record(ctx, task, label, ref or "-", ok,
                       "" if ok else (rest.split(":")[0] or "UNKNOWN"),
                       f"{kind} {name}".strip())
            print(f"total: {len(shown)} of {len(rows)}")
            # planned is unknown before the list arrives, so it is set here: a
            # receipt whose plan is 0 while rows exist would report every row as
            # unaccounted.
            store.set_task_planned(task.id, len(shown) if args.live else 0)
            close_task(ctx, task, derive=True)
            return 0
        if args.action == "spambot":
            rows = pool.spambot(args.accounts, live=bool(args.live),
                                wait=args.wait, apply=bool(args.apply))
            for label, kind, ok, detail in rows:
                print(f"{'ok  ' if ok else 'fail'} {label} "
                      f"kind={kind or '-'} {detail}")
            known = sum(1 for _, kind, _, _ in rows if kind)
            print(f"total: {len(rows)} classified: {known}")
            _account_rows_task(
                settings, store, "spambot",
                [(label, ok, f"kind={kind or '-'} {detail}")
                 for label, kind, ok, detail in rows],
                live=bool(args.live))
            return 0 if all(ok for _, _, ok, _ in rows) else 1
        if args.action == "list":
            rows = pool.list()
            if args.json:
                import json

                print(json.dumps([row.__dict__ for row in rows], ensure_ascii=False))
            else:
                for row in rows:
                    print(f"{row.label}\t{row.phone or '-'}\t{row.status}\t"
                          f"kind={row.kind or '-'}\t{row.session}")
                print(f"total: {len(rows)}")
            return 0
        rows = pool.test(args.accounts, live=bool(args.live))
        for label, ok, detail in rows:
            print(f"{'ok  ' if ok else 'fail'} {label} {detail}")
        ok_count = sum(1 for _, ok, _ in rows if ok)
        print(f"total: {len(rows)} ok: {ok_count}")
        _account_rows_task(settings, store, "test", rows, live=bool(args.live))
        return 0 if ok_count == len(rows) else 1
    finally:
        store.close()


class _Pool:
    """Small indirection so the CLI and the features share one pool type."""

    def __init__(self, settings: Settings, store: Store) -> None:
        from .accounts import AccountPool

        self._pool = AccountPool(settings, store)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pool, name)


def main() -> None:  # pragma: no cover - console script
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["build_parser", "main", "names", "run"]
