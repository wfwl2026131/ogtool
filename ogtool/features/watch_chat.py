"""#7 watch chat - watch chats and relay what survives the exclusion list.

Fields: the custom target list, the excluded keywords (comma or newline) and the
relay group.  A dry run replays a supplied set of incoming messages, so the
decision for each one is visible: relayed, or dropped by a keyword.  ``--live``
opens the sessions, registers one ``NewMessage`` handler per account and stays
up until Ctrl+C, so the exclusion list, the duplicate cache and the relay run on
real events rather than on the replay.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..config import ConfigError, Settings
from ..flags import Flag
from ..runtime import RunContext
from ..senders import Content, _unresolved, code_from_exception, peer_variants
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
from .auto_reply import DuplicateCache

FEATURE = "watch_chat"
SECTION = "watch_chat"
TITLE = "#7 监听聊天"

#: A relayed message is remembered this long, so a re-delivery after a
#: reconnect does not repost it.  Keyed by chat plus message id, so two equal
#: texts from the same chat still both go out.
DEDUPE_SECONDS = 300

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
class Counts:
    """What the run saw: messages, relays and drops, not target count."""

    watched: int = 0
    relayed: int = 0
    dropped: int = 0


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


def watch_peers(config: WatchConfig) -> list[object]:
    """The peers to subscribe to: a numeric id goes in as an int."""
    return [peer_variants(target)[0] for target in config.targets]


def relay_peers(relay: str) -> list[object]:
    """The forms of the relay chat, tried in order; empty when unset."""
    if not relay:
        return []
    return peer_variants(parse_many([relay], "group")[0])


def run(config: WatchConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts] or ["-"]
    relay = config.relay
    counts = Counts()

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
                     0 if ctx.live else len(config.incoming))
    if ctx.live:
        return _run_live(config, ctx, task, labels)

    sender = ctx.sender()
    for index, message in enumerate(config.incoming, start=1):
        if message.peer:
            target = parse_many([message.peer], "custom")[0]
        else:
            target = config.targets[0]
        account = labels[(index - 1) % len(labels)]
        counts.watched += 1
        hit = excluded(message.text, config.exclude)
        if hit:
            counts.dropped += 1
            record(ctx, task, account, target.ref, True, "excluded", hit)
            print(f"[{index:03d}] peer={target.ref} sender={message.sender or '-'} "
                  f"-> SKIP excluded by {hit!r}")
            continue
        if not relay:
            counts.dropped += 1
            print(f"[{index:03d}] peer={target.ref} -> SKIP no relay group")
            continue
        content = Content(text=message.text)
        result = sender.send(account, parse_many([relay], "group")[0], content)
        if result.ok:
            counts.relayed += 1
            record(ctx, task, account, relay, True)
            print(f"[{index:03d}] peer={target.ref} sender="
                  f"{message.sender or '-'} -> relayed to {relay}")
        else:
            counts.dropped += 1
            record(ctx, task, account, relay, False, result.code, result.detail)
            print(f"[{index:03d}] peer={target.ref} -> {result.code}")

    print(f"targets: {len(config.targets)}  watched: {counts.watched}  "
          f"relayed: {counts.relayed}  dropped: {counts.dropped}")
    print(f"PLAN: {len(config.incoming)} messages, {len(accounts)} accounts, "
          f"relayed {counts.relayed}")
    close_task(ctx, task, ok=counts.relayed, failed=counts.dropped)
    return 0


def _run_live(config: WatchConfig, ctx: RunContext, task,
              labels: list[str]) -> int:
    """Attach a NewMessage handler to every account and stay up until Ctrl+C."""
    try:
        from telethon import events
    except ImportError:  # pragma: no cover - live only
        print("ERROR: telethon is not installed")
        close_task(ctx, task, ok=0, failed=0, status="failed")
        return 2

    sender = ctx.sender()
    counts = Counts(watched=0, relayed=0, dropped=0)
    clients: list[tuple[str, object]] = []
    for account in ctx.bind(config.account_labels or None):
        try:
            clients.append((account.label, sender._client(account.label)))  # noqa: SLF001
        except Exception as error:  # noqa: BLE001 - reported and skipped
            ctx.note(f"{account.label}: {error}", "warn")
    if not clients:
        print("ERROR: no account could be opened for --live")
        close_task(ctx, task, ok=0, failed=0, status="failed")
        return 1

    peers = watch_peers(config)
    targets = relay_peers(config.relay)
    cache = DuplicateCache(DEDUPE_SECONDS)
    owner = {id(client): label for label, client in clients}
    fallback = labels[0] if labels else "-"

    def log_drop(account: str, peer: str, code: str, detail: str = "") -> None:
        counts.dropped += 1
        ctx.store.add_result(task.id, account, peer, "dropped", code, detail)

    @events.register(events.NewMessage(chats=peers, incoming=True))
    async def handler(event):  # noqa: ANN001, ANN202 - Telethon callback
        counts.watched += 1
        account = owner.get(id(getattr(event, "client", None)), fallback)
        chat = str(getattr(event, "chat_id", ""))
        ident = str(getattr(event, "id", "")) or (event.raw_text or "")
        body = event.raw_text or ""
        if cache.seen(chat, ident, datetime.now(UTC)):
            log_drop(account, chat, "DUPLICATE", ident)
            print(f"DROP duplicate peer={chat} id={ident}")
            return
        hit = excluded(body, config.exclude)
        if hit:
            log_drop(account, chat, "EXCLUDED", hit)
            print(f"DROP excluded by {hit!r} peer={chat}")
            return
        if not targets:
            log_drop(account, chat, "NO_RELAY")
            print(f"DROP no relay group peer={chat}")
            return
        message_id, code = await _relay(event, targets, body)
        if message_id:
            counts.relayed += 1
            ctx.store.add_result(task.id, account, config.relay, "sent",
                                 detail=f"{chat}#{ident}")
            print(f"RELAY peer={chat} id={ident} -> {config.relay} "
                  f"message={message_id}")
        else:
            log_drop(account, chat, code, "relay failed")
            print(f"RELAY peer={chat} -> {code}")

    for _label, client in clients:
        client.add_event_handler(handler)
    print(f"live: {len(clients)} account(s) watching {len(peers)} chat(s) "
          f"-> {config.relay or '-'}, Ctrl+C to stop")
    try:
        import asyncio

        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        ctx.note("interrupted by the operator", "warn")
    finally:
        for _label, client in clients:
            with contextlib.suppress(Exception):
                client.remove_event_handler(handler)
        close_task(ctx, task, ok=counts.relayed, failed=0)
        print(f"DONE: targets={len(config.targets)} watched={counts.watched} "
              f"relayed={counts.relayed} dropped={counts.dropped}")
    return 0


async def _relay(event, peers: list[object], body: str) -> tuple[int, str]:
    """Post the text to the relay chat; returns (message id, error code)."""
    code = "RELAY_FAILED"
    for peer in peers:
        try:
            message = await event.client.send_message(peer, body)
            return int(getattr(message, "id", 0) or 0), ""
        except Exception as error:  # noqa: BLE001 - the last error decides
            code = code_from_exception(error)[0]
            if not _unresolved(error):
                break
    return 0, code


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(WatchConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["Counts", "DEDUPE_SECONDS", "Incoming", "TITLE", "WatchConfig",
           "excluded", "main", "parse_incoming", "relay_peers", "run",
           "watch_peers"]
