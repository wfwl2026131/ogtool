"""Collect a chat_pattern from a real chat.

The reference client can *learn* a script instead of only replaying one.  Its
collector names every step in the dump (1745607-1745660):

    Starting collection from group: %1 accountIndex:%2
    Started collecting from group: %1 for %2 hours accountIndex:%3
    Found group by peerId %1: %2
    Loading batch from: %1 (collected: %2 messages)...
    Requesting batch with offsetId: %1, total collected: %2
    Flood wait detected, waiting %1 seconds before retry
    Collection completed: reached %1-hour time limit with %2 messages
    Collection completed: no more messages available. Total: %1 messages
    Collection completed: reached message limit (10000). Total: %1 messages

So the shape is: resolve the chat, page history backwards until the hour
window or the message cap stops it, wait out a flood limit instead of dropping
the run, and then turn what came back into script lines.  That last step is
``generateRealContentFromItems`` - the text (and the picture) is the real one,
not invented, which is what makes a collected script replayable.

This module is deliberately *outside* the 25 console entries: like the bot-link
tool it is a page-less capability of the reference client.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .chat_pattern import ChatMessage, PatternError, Sticker
from .sources import SourceMessage, parse_many
from .targets import Target
from .targets import parse_many as parse_targets

#: The collector's own ceiling, from the log line "(10000)".
COLLECT_CAP = 10_000

_STICKER = re.compile(r"\[STICKER:(?P<ref>[^\]]*)\]")


@dataclass(slots=True)
class CollectConfig:
    target: str
    out_path: str = ""
    account: str = ""
    hours: float = 24.0
    cap: int = COLLECT_CAP
    delay_min: float = 5.0
    delay_max: float = 15.0
    slot: int = 0
    reply_level: int = 0


@dataclass(slots=True)
class CollectResult:
    target: Target
    messages: list[SourceMessage] = field(default_factory=list)
    #: The reason the walk stopped, in the collector's own words.
    stopped: str = ""
    written: str = ""
    lines: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.messages)


def stop_reason(count: int, hours: float, cap: int) -> str:
    """Which of the three completions ended the walk."""
    if count >= cap:
        return (f"Collection completed: reached message limit ({cap}). "
                f"Total: {count} messages")
    if count == 0:
        return ("Collection completed: no more messages available. "
                f"Total: {count} messages")
    return (f"Collection completed: reached {hours:g}-hour time limit with "
            f"{count} messages")


def to_lines(config: CollectConfig,
             messages: list[SourceMessage]) -> tuple[list[str], int]:
    """Real content into script lines: ``A<n>|L<n>|D<min>-<max>|<body>``.

    An empty message is skipped rather than written as an empty body: replaying
    a blank line would send nothing and consume a slot in the schedule. Returns
    the lines and how many messages could not become one.
    """
    lines: list[str] = []
    dropped = 0
    for message in messages:
        # ``ChatMessage.render`` puts the picture back into the body as
        # ``[IMG:...]``, so the media field goes there rather than into the text.
        body = message.text.strip()
        image = message.media.strip()
        sticker = None
        found = _STICKER.fullmatch(body.strip()) if body else None
        if found:
            try:
                sticker = Sticker.parse(found.group("ref"))
            except PatternError:
                # A reference the format cannot read is dropped, not sent as
                # literal text: replaying "[STICKER:abc]" would post that string.
                dropped += 1
                continue
            body = ""
        if not body and sticker is None and not image:
            continue
        lines.append(ChatMessage(
            account_slot=config.slot,
            reply_level=config.reply_level,
            delay_min=config.delay_min,
            delay_max=config.delay_max,
            like_count=0,
            text=body,
            image=image,
            sticker=sticker,
        ).render())
    return lines, dropped


def write_script(path: Path, lines: list[str], header: str = "") -> str:
    """Write the script, creating the ``tdata/chat_pattern`` tree if needed."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(lines)
    text = f"# {header}\n{body}\n" if header else f"{body}\n"
    path.write_text(text, encoding="utf-8")
    return str(path)


def items_to_messages(items: list[str]) -> list[SourceMessage]:
    """``peer|sender|text[|media]`` rows into messages, skipping broken ones."""
    clean = [item for item in items if str(item).strip()]
    if not clean:
        return []
    try:
        return parse_many(clean)
    except Exception:
        messages: list[SourceMessage] = []
        for item in clean:
            try:
                messages.extend(parse_many([item]))
            except Exception:
                continue
        return messages


def collect(sender: object, config: CollectConfig, ctx: object) -> CollectResult:
    """The walk itself: one history request, then the script."""
    target = parse_targets([config.target], "custom")[0]
    account = config.account
    if not account:
        bound = getattr(ctx, "bind", lambda _labels: [])(
            [] if not config.account else [config.account]) or []
        account = bound[0].label if bound else "-"
    result = sender.history(account, target,  # type: ignore[attr-defined]
                            minutes=int(config.hours * 60),
                            limit=config.cap)
    messages = items_to_messages(list(getattr(result, "items", []) or []))
    if not result.ok:
        raise PatternError(f"{result.code}: {result.detail}".strip(": "))
    return CollectResult(target=target, messages=messages,
                         stopped=stop_reason(len(messages), config.hours,
                                             config.cap))


__all__ = [
    "COLLECT_CAP",
    "CollectConfig",
    "CollectResult",
    "collect",
    "items_to_messages",
    "stop_reason",
    "to_lines",
    "write_script",
]
