"""Command line entry point: ``python -m ogtool <feature> --config x.yaml``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import ConfigError, Settings, load_task, merge
from .features import get, load_defaults, names
from .loggers import task_logger
from .runtime import RunContext
from .store import Store

DEFAULT_CONFIG = "config.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ogtool",
        description="Offline toolkit for the 22 console features.",
    )
    parser.add_argument("--version", action="version",
                        version=f"ogtool {__version__}")
    parser.add_argument("--config", default=DEFAULT_CONFIG,
                        help=f"global config (default: {DEFAULT_CONFIG})")

    subs = parser.add_subparsers(dest="command", required=True)

    accounts = subs.add_parser("accounts", help="account pool")
    account_subs = accounts.add_subparsers(dest="action", required=True)

    add = account_subs.add_parser("add", help="add a phone number or a session")
    add.add_argument("--phone", default="", help="phone number, e.g. +8613800000000")
    add.add_argument("--session", default="", help="path to an existing .session")
    add.add_argument("--label", default="", help="name used in every other command")
    add.add_argument("--notes", default="")
    add.add_argument("--live", action="store_true",
                     help="talk to Telegram (default: dry-run only)")

    listing = account_subs.add_parser("list", help="list the pool")
    listing.add_argument("--json", action="store_true")

    test = account_subs.add_parser("test", help="connectivity test")
    test.add_argument("--accounts", nargs="*", default=None)
    test.add_argument("--live", action="store_true")

    for name, help_text in (
        ("mass_send", "console #5: send to private chats, groups, clients"),
        ("keep_online", "console #21: keep the sessions online"),
        ("auto_reply", "console #6: keyword rules with a duplicate cache"),
    ):
        feature = subs.add_parser(name, help=help_text)
        _common(feature)

    mass = subs.choices["mass_send"]
    mass.add_argument("--targets", default=None, help="comma separated entries")
    mass.add_argument("--targets-file", action="append", default=[],
                      help="file with one entry per line (repeatable)")
    mass.add_argument("--target-kind", default="custom",
                      choices=["private", "group", "channel", "client", "custom"])
    mass.add_argument("--text", default=None)
    mass.add_argument("--media", default=None)
    mass.add_argument("--forward-from", default=None)
    mass.add_argument("--interval-min", type=float, default=None)
    mass.add_argument("--interval-max", type=float, default=None)
    mass.add_argument("--group-interval", type=float, default=None)
    mass.add_argument("--threads", type=int, default=None)
    mass.add_argument("--daily-limit", type=int, default=None)
    mass.add_argument("--success-cap", type=int, default=None)
    mass.add_argument("--exec-cap", type=int, default=None)

    online = subs.choices["keep_online"]
    online.add_argument("--interval", type=float, default=None,
                        help="seconds between heartbeats")
    online.add_argument("--loops", type=int, default=None)

    reply = subs.choices["auto_reply"]
    reply.add_argument("--rule", action="append", default=[],
                       help="keyword=>reply (repeatable)")
    reply.add_argument("--duplicate-seconds", type=int, default=None)
    reply.add_argument("--simulate", action="append", default=[],
                       help="peer|sender|text (repeatable)")
    reply.add_argument("--disabled", action="store_true")

    return parser


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", default=None, help="task YAML merged over config")
    parser.add_argument("--accounts", nargs="*", default=None,
                        help="account labels (default: the whole pool)")
    parser.add_argument("--task-name", default=None)
    parser.add_argument("--live", action="store_true",
                        help="really talk to Telegram (default: dry-run)")
    parser.add_argument("--verbose", action="store_true")


def _overrides(args: argparse.Namespace) -> dict[str, Any]:
    """Turn the CLI flags into the same shape a task YAML has."""
    raw: dict[str, Any] = {}
    if args.accounts is not None:
        raw["accounts"] = args.accounts
    if args.task_name:
        raw["task_name"] = args.task_name

    if args.command == "mass_send":
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

    elif args.command == "keep_online":
        online: dict[str, Any] = {}
        if args.interval is not None:
            online["interval"] = args.interval
        if args.loops is not None:
            online["loops"] = args.loops
        if online:
            raw["keep_online"] = online

    elif args.command == "auto_reply":
        section: dict[str, Any] = {}
        if args.rule:
            section["rules"] = list(args.rule)
        if args.duplicate_seconds is not None:
            section["duplicate_seconds"] = args.duplicate_seconds
        if args.disabled:
            section["enabled"] = False
        if section:
            raw["auto_reply"] = section
        if args.simulate:
            raw["simulate"] = list(args.simulate)
    return raw


def _settings(path: str) -> Settings:
    target = Path(path).expanduser()
    if not target.exists():
        raise ConfigError(
            f"{target} not found; copy config.example.yaml to config.yaml"
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

    if args.command == "accounts":
        return _accounts(args, settings)

    base = settings.raw
    if getattr(args, "task", None):
        try:
            base = load_task(args.task, base)
        except ConfigError as error:
            print(f"ERROR: {error}")
            return 2
    raw = merge(base, _overrides(args))

    logger = task_logger(settings.log_dir, raw.get("task_name")
                         or args.command,
                         level=10 if args.verbose else 20)
    store = Store(settings.db_path)
    ctx = RunContext(settings=settings, store=store, log=logger,
                     live=bool(args.live))
    try:
        return get(args.command)(raw, ctx)
    except ConfigError as error:
        print(f"ERROR: {error}")
        return 2
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print("interrupted")
        return 130
    finally:
        ctx.close()
        store.close()


def _accounts(args: argparse.Namespace, settings: Settings) -> int:
    store = Store(settings.db_path)
    pool = _Pool(settings, store)
    try:
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
        print(f"total: {len(rows)} ok: {sum(1 for _, ok, _ in rows if ok)}")
        return 0
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
