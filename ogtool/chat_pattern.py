"""The chat_pattern script format the reference client replays.

One script line is an optional reply prefix plus four ``|`` separated fields::

    [replyPrefix]A<n>|L<n>|D<min>-<max>|<body>

``replyPrefix`` is one ``-`` per reply level, ``A<n>`` is an account *slot*
(mapped to a real account at run time), ``L<n>`` is the reaction count and
``D`` is the delay window in seconds.  The body may carry ``[STICKER:...]``
and ``[IMG:...]`` references; whatever is left is the text.  ``#`` starts a
comment line, which never gets sent.

The grammar is taken from the reference client's own format strings, so a file
written there round-trips through :meth:`ChatMessage.render` unchanged.
"""

from __future__ import annotations

import random
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

#: Where the reference client keeps scripts and their pictures.
PATTERN_DIR = Path("tdata") / "chat_pattern"
PICTURE_DIR = Path("tdata") / "chat_pattern" / "pic"


class PatternError(Exception):
    """A line, a file or an archive does not fit the format."""


@dataclass(slots=True)
class Sticker:
    """One ``[STICKER:...]`` reference, in any of its three spellings."""

    raw: str
    short_name: str = ""
    set_id: str = ""
    access_hash: str = ""
    emoji: str = ""

    @classmethod
    def parse(cls, raw: str) -> Sticker:
        """``shortName/x.webp``, ``unknown/setId/hash/x.webp`` or ``unknown/setId/x``."""
        text = raw.strip()
        parts = text.split("/")
        if not text:
            raise PatternError("empty sticker reference")
        if parts[0] == "unknown":
            if len(parts) < 3:
                raise PatternError(f"sticker without a picture: {text}")
            emoji = parts[-1]
            if len(parts) >= 4:
                return cls(raw=text, set_id=parts[1],
                           access_hash=parts[2], emoji=emoji)
            return cls(raw=text, set_id=parts[1], emoji=emoji)
        if len(parts) < 2:
            raise PatternError(f"sticker without a picture: {text}")
        return cls(raw=text, short_name=parts[0], emoji=parts[-1])

    def display(self) -> str:
        if self.short_name:
            return f"{self.short_name}/{self.emoji}" if self.emoji \
                else self.short_name
        known = "/".join(part for part in (self.set_id, self.access_hash)
                         if part)
        return f"unknown/{known}" if known else self.raw


@dataclass(slots=True)
class ChatMessage:
    """One script line, ready to be scheduled and sent."""

    account_slot: int = 0
    reply_level: int = 0
    delay_min: float = 0.0
    delay_max: float = 0.0
    like_count: int = 0
    text: str = ""
    image: str = ""
    sticker: Sticker | None = None

    def delay(self, rng: random.Random | None = None) -> float:
        """A wait inside ``[delay_min, delay_max]``, inclusive."""
        if self.delay_max <= self.delay_min:
            return float(self.delay_min)
        generator = rng or random
        return generator.uniform(self.delay_min, self.delay_max)

    def render(self) -> str:
        """Back to a script line, so a captured script can be replayed."""
        prefix = "-" * max(0, self.reply_level)
        delay = f"{_number(self.delay_min)}-{_number(self.delay_max)}"
        body = " ".join(part for part in (
            self.text,
            f"[IMG:{self.image}]" if self.image else "",
            f"[STICKER:{self.sticker.raw}]" if self.sticker else "",
        ) if part)
        return (f"{prefix}A{self.account_slot}|L{self.like_count}|"
                f"D{delay}|{body}")


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


_LINE = re.compile(r"^A(?P<slot>\d+)\|L(?P<likes>\d+)\|"
                   r"D(?P<low>\d+)(?:-(?P<high>\d+))?\|(?P<body>.*)$",
                   re.DOTALL)
_STICKER = re.compile(r"\[STICKER:(?P<ref>[^\]]*)\]")
_IMAGE = re.compile(r"\[IMG:(?P<name>[^\]]*)\]")


def parse_line(line: str, number: int = 0) -> ChatMessage | None:
    """One line -> one message; ``None`` for a blank line or a comment."""
    text = line.rstrip("\r\n")
    stripped = text.strip()
    if not stripped or stripped.startswith("#"):
        return None

    level = 0
    while text.startswith("-"):
        level += 1
        text = text[1:]

    fields = text.split("|", 3)
    if len(fields) != 4:
        raise PatternError(
            f"line {number}: expected A|L|D|body with four fields, "
            f"got {len(fields)} in {line.strip()!r}"
        )
    match = _LINE.match(text)
    if match is None:
        raise PatternError(
            f"line {number}: fields are not A<n>|L<n>|D<n>-<n>|body: "
            f"{line.strip()!r}"
        )
    low = float(match.group("low"))
    high = float(match.group("high") or match.group("low"))
    if high < low:
        low, high = high, low

    caption, image, sticker = _split_body(match.group("body"))
    return ChatMessage(
        account_slot=int(match.group("slot")),
        reply_level=level,
        delay_min=low,
        delay_max=high,
        like_count=int(match.group("likes")),
        text=caption,
        image=image,
        sticker=sticker,
    )


def _split_body(body: str) -> tuple[str, str, Sticker | None]:
    """Split a body into (text, image name, sticker)."""
    image = ""
    found = _IMAGE.search(body)
    if found:
        image = found.group("name").strip()
        if not image:
            raise PatternError("empty [IMG:] reference")
    sticker: Sticker | None = None
    found = _STICKER.search(body)
    if found:
        sticker = Sticker.parse(found.group("ref"))
    caption = _STICKER.sub("", _IMAGE.sub("", body)).strip()
    return caption, image, sticker


def parse_lines(lines: list[str], *, source: str = "") -> list[ChatMessage]:
    """Parse a script, skipping comments, naming the file on an error."""
    out: list[ChatMessage] = []
    for number, line in enumerate(lines, start=1):
        try:
            message = parse_line(line, number)
        except PatternError as error:
            where = f"{source}: " if source else ""
            raise PatternError(f"{where}{error}") from error
        if message is not None:
            out.append(message)
    return out


def parse_text(text: str, *, source: str = "") -> list[ChatMessage]:
    return parse_lines(text.splitlines(), source=source)


def load(path: str | Path) -> list[ChatMessage]:
    """Read one ``.txt`` script; UTF-8, BOM and CRLF both tolerated."""
    target = Path(path).expanduser()
    if not target.exists():
        raise PatternError(f"script not found: {target}")
    body = target.read_text(encoding="utf-8-sig")
    messages = parse_text(body, source=target.name)
    if not messages:
        raise PatternError(f"{target.name}: no message line")
    return messages


def assign_slots(messages: list[ChatMessage],
                 accounts: list[str]) -> dict[int, str]:
    """Slot number -> account label, in the order the slots first appear."""
    slots: list[int] = []
    for message in messages:
        if message.account_slot not in slots:
            slots.append(message.account_slot)
    if not accounts:
        return {slot: f"A{slot}" for slot in slots}
    return {slot: accounts[index % len(accounts)]
            for index, slot in enumerate(slots)}


def multiply(messages: list[ChatMessage], factor: int) -> list[ChatMessage]:
    """``消息倍率``: every message repeated ``factor`` times, same order."""
    factor = max(1, int(factor))
    return [message for message in messages for _ in range(factor)]


def total_delay(messages: list[ChatMessage]) -> float:
    return sum(message.delay_max for message in messages)


def import_zip(archive: str | Path, pattern_dir: str | Path,
               picture_dir: str | Path,
               now: datetime | None = None) -> Path:
    """Unpack a script archive: first ``.txt`` in, top level ``pic/`` merged.

    The saved name is ``<stem>_<yyyyMMdd_hhmmss>.<ext>``, the template the
    reference client uses when it refuses to overwrite an existing script.
    """
    source = Path(archive).expanduser()
    if not source.exists():
        raise PatternError(f"archive not found: {source}")
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    with tempfile.TemporaryDirectory(prefix="og_pattern_") as workdir:
        try:
            with zipfile.ZipFile(source) as bundle:
                order = [name for name in bundle.namelist()
                         if name.lower().endswith(".txt")
                         and not name.endswith("/")]
                bundle.extractall(workdir)
        except zipfile.BadZipFile as error:
            raise PatternError(f"{source.name}: not a zip archive") from error
        root = Path(workdir)
        if not order:
            raise PatternError("No txt pattern file found in zip")
        # Archive order, not alphabetical: the client takes the first entry.
        chosen = root / order[0]
        target_dir = Path(pattern_dir).expanduser()
        target_dir.mkdir(parents=True, exist_ok=True)
        saved = target_dir / f"{chosen.stem}_{stamp}{chosen.suffix or '.txt'}"
        shutil.copyfile(chosen, saved)

        pictures = root / "pic"
        if pictures.is_dir():
            destination = Path(picture_dir).expanduser()
            destination.mkdir(parents=True, exist_ok=True)
            for picture in pictures.iterdir():
                if picture.is_file():
                    shutil.copyfile(picture, destination / picture.name)
    return saved


def default_pattern_dir(data_dir: str | Path) -> Path:
    """``<data>/tdata/chat_pattern``: the layout the client uses."""
    return Path(data_dir).expanduser() / PATTERN_DIR


def default_picture_dir(data_dir: str | Path) -> Path:
    return Path(data_dir).expanduser() / PICTURE_DIR


__all__ = [
    "ChatMessage",
    "PATTERN_DIR",
    "PICTURE_DIR",
    "PatternError",
    "Sticker",
    "assign_slots",
    "default_pattern_dir",
    "default_picture_dir",
    "import_zip",
    "load",
    "multiply",
    "parse_line",
    "parse_lines",
    "parse_text",
    "total_delay",
]
