"""Command line entry point: ``python -m ogtool <feature> --config x.yaml``.

``--config`` is a global option and therefore goes **before** the feature name;
a task file is passed with ``--task`` on the feature itself.  Everything a page
offers is also settable in the task YAML, and the flags are derived from the
same field list, so the two can never drift apart.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .accounts import AccountError
from .config import ConfigError, Settings, load_task, merge
from .features import get, load_defaults, module, names
from .flags import section
from .loggers import task_logger
from .runtime import RunContext
from .simulate import Script
from .store import Store
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
    "puppet_chat_v2",
    "keep_online",
    "mass_send_v2",
    "mix_market",
    "auto_script",
    "export_accounts",
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
}


def build_parser() -> argparse.ArgumentParser:
    load_defaults()
    parser = argparse.ArgumentParser(
        prog="ogtool",
        # No abbreviations: "--news" must not silently become "--news-channel".
        allow_abbrev=False,
        description="Offline toolkit for the 22 console features.",
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
    _settings_parser(subs)

    for name in ORDER:
        feature = subs.add_parser(name, help=HELP.get(name, name))
        _common(feature)
        for flag in getattr(module(name), "FLAGS", ()):
            feature.add_argument(flag.name, **flag.to_kwargs())

    _mass_send_parser(subs.choices["mass_send"])
    _auto_reply_parser(subs.choices["auto_reply"])
    _keep_online_parser(subs.choices["keep_online"])
    return parser


def _settings_parser(subs: argparse._SubParsersAction) -> None:
    """The global knobs of #24, shown and validated, never acted on."""
    settings = subs.add_parser("settings",
                               help="#24 settings: show the global knobs")
    settings.add_argument("--json", action="store_true")


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

    test = account_subs.add_parser("test", help="connectivity test")
    test.add_argument("--accounts", nargs="*", default=None)
    test.add_argument("--dry-run", action="store_true", help="the default")
    test.add_argument("--live", action="store_true")

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


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", default=None, help="task YAML merged over config")
    parser.add_argument("--accounts", nargs="*", default=None,
                        help="account labels (default: the whole pool)")
    parser.add_argument("--task-name", default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="plan only, send nothing (the default)")
    parser.add_argument("--live", action="store_true",
                        help="really talk to Telegram (default: dry-run)")
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


def _auto_reply_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--rule", action="append", default=[],
                        help="keyword=>reply (repeatable)")
    parser.add_argument("--duplicate-seconds", type=int, default=None)
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
    if args.accounts is not None:
        raw["accounts"] = args.accounts
    if args.task_name:
        raw["task_name"] = args.task_name

    if args.command == "mass_send":
        return raw | _mass_send_overrides(args)
    if args.command == "auto_reply":
        return raw | _auto_reply_overrides(args)
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

    if args.command == "accounts":
        return _accounts(args, settings)
    if args.command == "settings":
        return _show_settings(settings, as_json=bool(args.json))

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
                         level=10 if args.verbose else 20)
    store = Store(settings.db_path)
    ctx = RunContext(settings=settings, store=store, log=logger,
                     live=bool(args.live),
                     script=Script.parse(raw.get("simulate_responses")))
    try:
        return get(args.command)(raw, ctx)
    except ConfigError as error:
        print(f"ERROR: {error}")
        return 2
    except (AccountError, TargetError) as error:
        print(f"ERROR: {error}")
        return 2
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print("interrupted")
        return 130
    finally:
        ctx.close()
        store.close()


def _show_settings(settings: Settings, as_json: bool = False) -> int:
    """Print the global knobs from the settings page, with the licence ones out."""
    runtime = settings.runtime
    rows = [
        ("account_batch", "账号分批加载数量", runtime.account_batch),
        ("threads_max", "全局线程池最大线程数", runtime.threads_max),
        ("resume_delay", "恢复任务延迟", runtime.resume_delay),
        ("tp_overload", "TP超载值", runtime.tp_overload),
        ("restart_interval", "重启间隔", runtime.restart_interval),
        ("restart_timed", "定时重启", runtime.restart_timed),
    ]
    if as_json:
        import json

        print(json.dumps({key: value for key, _label, value in rows},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"config: {settings.path}")
    print("runtime settings (no licence keys, restart is config only):")
    for key, label, value in rows:
        print(f"  {key:<18} {label:<14} {value}")
    due = (f"{runtime.restart_interval:.0f}s uptime"
           if runtime.restart_interval > 0 else "off")
    print(f"restart due at: {due}")
    print(f"overload check: "
          f"{'off' if runtime.tp_overload <= 0 else runtime.tp_overload}")
    return 0


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
        if args.action == "list":
            rows = pool.list()
            if args.json:
                import json

                print(json.dumps([row.__dict__ for row in rows], ensure_ascii=False))
            else:
                for row in rows:
                    print(f"{row.label}\t{row.phone or '-'}\t{row.status}\t"
                          f"{row.session}")
                print(f"total: {len(rows)}")
            return 0
        rows = pool.test(args.accounts, live=bool(args.live))
        for label, ok, detail in rows:
            print(f"{'ok  ' if ok else 'fail'} {label} {detail}")
        ok_count = sum(1 for _, ok, _ in rows if ok)
        print(f"total: {len(rows)} ok: {ok_count}")
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
