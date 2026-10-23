"""One description of a page's fields, used by both the CLI and the config.

Each feature module lists the fields its reference page shows; the CLI turns
that list into ``--flags`` and the feature turns the same names into its
config, so a field can never exist in one and not the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Flag:
    """One ``--flag`` -> one key in the task file's section."""

    name: str
    dest: str
    kind: type | None = None
    help: str = ""
    action: str = "store"

    def to_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"dest": self.dest, "help": self.help}
        if self.kind is not None and self.action == "store":
            kwargs["type"] = self.kind
        if self.action == "append":
            kwargs["action"] = "append"
            kwargs["default"] = None
        elif self.action == "store_true":
            kwargs["action"] = "store_true"
            kwargs["default"] = None
        else:
            kwargs["default"] = None
        return kwargs


def section(args: Any, flags: tuple[Flag, ...]) -> dict[str, Any]:
    """The part of the task YAML these flags override, empty when untouched."""
    out: dict[str, Any] = {}
    for flag in flags:
        value = getattr(args, flag.dest, None)
        if value is None:
            continue
        out[flag.dest] = value
    return out
