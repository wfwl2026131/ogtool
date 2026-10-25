"""Source messages for the pages that read a chat instead of writing blind.

``copy_chat`` and ``ai_clone`` copy what was said somewhere else, and
``ai_comment`` comments on posts.  Offline there is no chat to read, so the
task file supplies the history instead::

    messages:
      - "@source|@alice|hello there"
      - "@source|@bob|look at this|photo.jpg"

Four fields are ``peer|sender|text|media``; the media one is optional.  The
same parser reads the ``--simulate-messages`` flag, so a dry run and a task
file describe the source in exactly one way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import ConfigError
from .report import read_lines


@dataclass(slots=True)
class SourceMessage:
    """One captured message: who said it, where, and what came with it."""

    peer: str
    sender: str
    text: str
    media: str = ""
    message_id: int = 0
    #: What the capture knew about the message that its text does not say:
    #: ``bot``, ``pinned``, ``admin``. Empty means "not known", which the
    #: filters treat as "do not drop on this ground".
    flags: tuple[str, ...] = ()

    def display(self) -> str:
        return f"{self.sender}: {self.text or self.media or '(empty)'}"


def parse_entry(entry: str) -> SourceMessage:
    """``peer|sender|text`` or ``peer|sender|text|media`` -> one message."""
    text = entry.strip()
    if not text:
        raise ConfigError("empty source message")
    parts = text.split("|")
    if len(parts) < 3:
        raise ConfigError(
            f"source message needs peer|sender|text, got {entry!r}"
        )
    peer, sender, body = (part.strip() for part in parts[:3])
    media = parts[3].strip() if len(parts) > 3 else ""
    flags: tuple[str, ...] = ()
    if len(parts) > 4:
        # The fifth segment is the flag list: ``flags=bot,pinned``.
        tail = parts[4].strip()
        if tail.startswith("flags="):
            flags = tuple(part.strip().lower()
                          for part in tail[len("flags="):].split(",")
                          if part.strip())
    if not peer:
        raise ConfigError(f"source message without a peer: {entry!r}")
    return SourceMessage(peer=peer, sender=sender, text=body, media=media,
                         flags=flags)


def parse_many(entries: list[str] | tuple[str, ...]) -> list[SourceMessage]:
    """Every entry, numbered in the order given (the id a plan refers to)."""
    messages: list[SourceMessage] = []
    for entry in entries:
        message = parse_entry(str(entry))
        message.message_id = len(messages) + 1
        messages.append(message)
    return messages


def from_config(data: dict, key: str = "messages") -> list[SourceMessage]:
    """The source list of one section: a YAML list, one blob, or files."""
    value = data.get(key)
    entries: list[str] = []
    if isinstance(value, (list, tuple)):
        entries = [str(item) for item in value]
    elif isinstance(value, str):
        entries = value.splitlines()
    elif value is not None:
        raise ConfigError(f"{key}: expected a list of peer|sender|text lines")

    files = data.get(f"{key}_files") or data.get("message_files") or []
    if isinstance(files, str):
        files = [files]
    for path in files:
        entries.extend(read_lines(str(Path(str(path)).expanduser())))
    return parse_many([entry for entry in entries if entry.strip()])


def synthetic(peer: str, count: int, *, prefix: str = "post") -> list[SourceMessage]:
    """Stand-in posts, so the comment page has something to poll offline."""
    count = max(0, int(count))
    return [SourceMessage(peer=peer, sender="channel", text=f"{prefix} {index}",
                          message_id=index)
            for index in range(1, count + 1)]


def keep(message: SourceMessage, keywords: list[str]) -> bool:
    """``False`` when any keyword appears in the text (case insensitive)."""
    body = message.text.casefold()
    return not any(word.casefold() in body for word in keywords if word)


def by_peer(messages: list[SourceMessage], peers: list[str]) -> list[SourceMessage]:
    """Only the messages from these chats; empty ``peers`` keeps everything."""
    if not peers:
        return list(messages)
    wanted = {peer.casefold() for peer in peers}
    return [message for message in messages
            if message.peer.casefold() in wanted]


def replace_keywords(text: str, rules: list[tuple[str, str]]) -> str:
    """Apply ``left=right`` rewrites in order, left side case insensitive."""
    out = text
    for left, right in rules:
        if not left:
            continue
        out = out.replace(left, right)
    return out


def parse_replacements(entries: list[str]) -> list[tuple[str, str]]:
    """``left=right`` lines -> pairs; a line without ``=`` is an error."""
    pairs: list[tuple[str, str]] = []
    for entry in entries:
        line = str(entry).strip()
        if not line:
            continue
        if "=" not in line:
            raise ConfigError(
                f"replacement needs left=right, got {line!r}"
            )
        left, right = line.split("=", 1)
        if not left.strip():
            raise ConfigError(f"replacement without a left side: {line!r}")
        pairs.append((left.strip(), right.strip()))
    return pairs


@dataclass(slots=True)
class Collected:
    """What the capture step produced, plus why anything was dropped."""

    kept: list[SourceMessage] = field(default_factory=list)
    dropped: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    scanned: int = 0

    def drop(self, reason: str) -> None:
        self.dropped += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1

    def summary(self) -> str:
        parts = [f"captured {len(self.kept)}/{self.scanned}"]
        for reason in sorted(self.reasons):
            parts.append(f"{reason}={self.reasons[reason]}")
        return "  ".join(parts)


__all__ = [
    "Collected",
    "SourceMessage",
    "by_peer",
    "from_config",
    "keep",
    "parse_entry",
    "parse_many",
    "parse_replacements",
    "replace_keywords",
    "synthetic",
]
