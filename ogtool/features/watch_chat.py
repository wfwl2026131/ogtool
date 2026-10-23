"""#7 监听聊天 — watch chats and relay what survives the exclusion list.

Fields: the custom target list, the excluded keywords (comma or newline) and the
relay group.  A dry run replays a supplied set of incoming messages, so the
decision for each one is visible: relayed, or dropped by a keyword.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import ConfigError, Settings
from ..flags import Flag
from ..runtime import RunContext
from ..senders import Content
from ..targets import Target, parse_many
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    lines,
    name_of,
    open_task,
    record,
    section,
    targets_of,
    text,
)

FEATURE = "watch_chat"
SECTION = "watch_chat"
TITLE = "#7 监听聊天"

FLAGS = (
    Flag("--targets", "targets", str, "chats to watch, comma separated"),
    Flag("--targets-file", "files", str, "file with one target per line",
         action="append"),
    Flag("--exclude", "exclude", str, "excluded keywords, comma separated"),
    Flag("--relay", "relay", str, "relay group link"),
    Flag("--simulate", "simulate", str, "peer|sender|text (repeatable)",
         action="append"),
)


@dataclass(slots=True)
class Incoming:
    peer: str
    sender: str
    text: str


@dataclass(slots=True)
class WatchConfig:
    account_labels: list[str] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    relay: str = ""
    incoming: list[Incoming] = field(default_factory=list)
    task_name: str = "watch-chat"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> WatchConfig:
        del settings
        data = section(raw, SECTION)
        watch_targets = targets_of(data, kind="custom")
        if not watch_targets:
            raise ConfigError(f"{SECTION}: no chat to watch (--targets)")
        return cls(
            account_labels=accounts_of(raw, data),
            targets=watch_targets,
            exclude=lines(data, "exclude"),
            relay=text(data, "relay"),
            incoming=parse_incoming(
                data.get("messages") or data.get("simulate") or []),
            task_name=name_of(raw, data, "watch-chat"),
        )


def parse_incoming(entries: list) -> list[Incoming]:
    """``peer|sender|text`` rows; a bare line is treated as the text."""
    out: list[Incoming] = []
    for entry in entries:
        parts = str(entry).split("|", 2)
        if len(parts) != 3:
            parts = ["", "", str(entry)]
        out.append(Incoming(parts[0].strip(), parts[1].strip(),
                            parts[2].strip()))
    return out


def excluded(text: str, keywords: list[str]) -> str:
    """The keyword that drops this message, or an empty string."""
    lowered = text.lower()
    for keyword in keywords:
        if keyword and keyword.lower() in lowered:
            return keyword
    return ""


def run(config: WatchConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts] or ["-"]
    relay = config.relay

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        targets=", ".join(target.ref for target in config.targets),
        exclude=", ".join(config.exclude) or "-",
        relay=relay or "-",
        replay=len(config.incoming),
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"targets": [t.ref for t in config.targets],
                      "exclude": config.exclude, "relay": relay},
                     len(config.incoming))
    sender = ctx.sender()

    relayed = dropped = 0
    for index, message in enumerate(config.incoming, start=1):
        if message.peer:
            target = parse_many([message.peer], "custom")[0]
        else:
            target = config.targets[0]
        account = labels[(index - 1) % len(labels)]
        hit = excluded(message.text, config.exclude)
        if hit:
            dropped += 1
            record(ctx, task, account, target.ref, True, "excluded", hit)
            print(f"[{index:03d}] peer={target.ref} sender={message.sender or '-'} "
                  f"-> SKIP excluded by {hit!r}")
            continue
        if not relay:
            dropped += 1
            print(f"[{index:03d}] peer={target.ref} -> SKIP no relay group")
            continue
        content = Content(text=message.text)
        result = sender.send(account, parse_many([relay], "group")[0], content)
        if result.ok:
            relayed += 1
            record(ctx, task, account, relay, True)
            print(f"[{index:03d}] peer={target.ref} sender="
                  f"{message.sender or '-'} -> relayed to {relay}")
        else:
            dropped += 1
            record(ctx, task, account, relay, False, result.code)
            print(f"[{index:03d}] peer={target.ref} -> {result.code}")

    print(f"watched: {len(config.targets)}  relayed: {relayed}  "
          f"dropped: {dropped}")
    print(f"PLAN: {len(config.incoming)} messages, {len(accounts)} accounts, "
          f"relayed {relayed}")
    close_task(ctx, task, ok=relayed, failed=dropped)
    return 0


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(WatchConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["Incoming", "TITLE", "WatchConfig", "excluded", "main",
           "parse_incoming", "run"]
