"""Prompt templates, kept in ``prompts/*.txt`` instead of inside the code.

The wording a page sends to the model is content, not logic: it is edited far
more often than the code around it, and the same template has to be auditable
after a run.  So each template is a plain text file with ``{{placeholders}}``,
and this module is the only place that reads them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from .config import ConfigError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .config import Settings

#: The templates ship inside the package (``ogtool/prompts/*.txt``), so the
#: editable checkout and the installed wheel resolve to the same directory and
#: no page can die on a missing template after ``pip install``.
DEFAULT_DIR = Path(__file__).resolve().parent / "prompts"

_PLACEHOLDER = re.compile(r"\{\{\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

#: The templates every page here expects to find.
NAMES = ("ai_chat", "ai_comment", "ai_clone", "ai_polish", "translate")


def directory(settings: Settings | None = None) -> Path:
    """Where templates live: ``prompts`` in the task file, else the default."""
    if settings is not None:
        named = str((settings.raw or {}).get("prompts") or "").strip()
        if named:
            return Path(named).expanduser()
    return DEFAULT_DIR


def load(name: str, *, folder: Path | None = None) -> str:
    """One template, as text. A missing or empty file is a config error."""
    path = (folder or DEFAULT_DIR) / f"{name}.txt"
    if not path.exists():
        raise ConfigError(
            f"prompt template not found: {path} "
            f"(expected one of {', '.join(NAMES)})"
        )
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ConfigError(f"prompt template is empty: {path}")
    return text


def render(template: str, **values: object) -> str:
    """Fill ``{{name}}``; an unfilled placeholder is a config error."""

    def replace(match: re.Match[str]) -> str:
        key = match.group("name")
        if key not in values:
            raise ConfigError(f"prompt placeholder {{{{{key}}}}} has no value")
        return str(values[key])

    return _PLACEHOLDER.sub(replace, template)


def compose(name: str, *, settings: Settings | None = None,
            folder: Path | None = None, **values: object) -> str:
    """Load one template and fill it in one step."""
    where = folder or directory(settings)
    return render(load(name, folder=where), **values)


def placeholders(template: str) -> list[str]:
    """Every ``{{name}}`` in a template, for the self checks and the tests."""
    return sorted({match.group("name") for match in _PLACEHOLDER.finditer(template)})


__all__ = ["DEFAULT_DIR", "NAMES", "compose", "directory", "load",
           "placeholders", "render"]
