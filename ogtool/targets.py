"""Target parsing: usernames, links, numeric ids and phone numbers.

The reference client accepts one entry per line and says so in its label
("custom targets: one per line, id / username / phone / invite link"), so
this module keeps four kinds and normalises whatever the user typed into
one of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


class TargetError(Exception):
    """An entry cannot be turned into a target."""


@dataclass(frozen=True, slots=True)
class Target:
    kind: str      # private | group | channel | custom
    ref: str       # @name, numeric id, +phone or invite hash
    title: str = ""

    def display(self) -> str:
        return self.title or self.ref


_USERNAME = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{0,31})$")
_PHONE = re.compile(r"^\+?\d{6,15}$")
_TME = re.compile(
    r"^(?:https?://)?(?:t\.me|telegram\.me)/(?P<rest>.+)$", re.IGNORECASE
)
_INVITE = re.compile(r"^(?:joinchat/|\+|)(?P<hash>[A-Za-z0-9_-]{10,})$")


def parse_entry(entry: str, kind: str = "custom") -> Target:
    """Turn one line of a target box into a :class:`Target`."""
    text = entry.strip()
    if not text:
        raise TargetError("empty target entry")
    if text.startswith("#"):          # a commented line in a list
        raise TargetError("comment")

    link = _TME.match(text)
    if link:
        rest = link.group("rest").strip("/")
        if rest.startswith("joinchat/") or rest.startswith("+"):
            hash_part = rest.split("/", 1)[-1].lstrip("+")
            if not hash_part:
                raise TargetError(f"invite link without a hash: {text}")
            return Target("group", f"+{hash_part}")
        if rest.startswith("c/"):
            return Target("channel", rest)
        name = _USERNAME.match(rest.split("/", 1)[0].split("?")[0])
        if not name:
            raise TargetError(f"unrecognised link: {text}")
        return Target(kind if kind != "custom" else "channel", f"@{name.group(1)}")

    if _PHONE.match(text):
        return Target("private", text if text.startswith("+") else f"+{text}")

    if text.lstrip("-").isdigit():
        return Target(kind if kind != "custom" else "private", text)

    name = _USERNAME.match(text)
    if name:
        return Target(kind if kind != "custom" else "private", f"@{name.group(1)}")

    invite = _INVITE.match(text)
    if invite:
        return Target("group", f"+{invite.group('hash')}")

    raise TargetError(f"cannot read target: {text}")


def parse_many(entries: list[str], kind: str = "custom") -> list[Target]:
    """Parse a list of entries, dropping comments and duplicates."""
    seen: set[str] = set()
    result: list[Target] = []
    for entry in entries:
        try:
            target = parse_entry(entry, kind)
        except TargetError as error:
            if str(error) == "comment":
                continue
            raise
        # The same account can be written as @name or as a t.me link; the
        # kind we guessed differs, the reference does not, so dedupe on ref.
        if target.ref in seen:
            continue
        seen.add(target.ref)
        result.append(target)
    return result


def load_list(path: str | Path, kind: str = "custom") -> list[Target]:
    """Read a target file, one entry per line."""
    source = Path(path).expanduser()
    if not source.exists():
        raise TargetError(f"target file not found: {source}")
    return parse_many(source.read_text(encoding="utf-8").splitlines(), kind)


def split_inline(value: str) -> list[str]:
    """Split a comma separated CLI value into entries."""
    return [part.strip() for part in value.split(",") if part.strip()]


def collect(
    inline: str | None = None,
    files: list[str] | None = None,
    kind: str = "custom",
) -> list[Target]:
    """Everything a feature needs: ``--targets`` plus ``--targets-file``."""
    entries: list[str] = []
    targets: list[Target] = []
    if inline:
        entries.extend(split_inline(inline))
    if files:
        for item in files:
            targets.extend(load_list(item, kind))
    if entries:
        targets.extend(parse_many(entries, kind))
    unique: list[Target] = []
    seen: set[str] = set()
    for target in targets:
        if target.ref not in seen:
            seen.add(target.ref)
            unique.append(target)
    return unique
