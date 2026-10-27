"""Files a page reads (a .txt list) and files it writes (MISSING, FLOOD, report).

The reference pages export their leftovers next to the task log, so a later run
can retry only what failed.  Paths here are always under the task directory
unless the task file names an absolute one.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def read_lines(path: str | Path, *, skip_comments: bool = True) -> list[str]:
    """One entry per line, blanks dropped, ``#`` lines dropped by default."""
    source = Path(path).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"file not found: {source}")
    out: list[str] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        if skip_comments and text.startswith("#"):
            continue
        out.append(text)
    return out


def dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def write_lines(path: str | Path, items: Iterable[str],
                header: str = "") -> Path:
    """Write one entry per line, creating the parent directory."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = list(items)
    body = []
    if header:
        body.append(f"# {header}")
    body.extend(rows)
    target.write_text("\n".join(body) + ("\n" if body else ""), encoding="utf-8")
    return target


def export(path: str | Path, items: Iterable[str], name: str = "list") -> Path:
    """Write an export and print the line the page shows for it."""
    target = write_lines(path, items, header=name)
    count = len(list(items)) if not isinstance(items, list) else len(items)
    print(f"exported {name} -> {target} ({count} entries)")
    return target


def write_report(path: str | Path, title: str,
                 sections: dict[str, Iterable[str]]) -> Path:
    """A small markdown report: one heading per bucket."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", ""]
    for name, rows in sections.items():
        rows = list(rows)
        lines.append(f"## {name} ({len(rows)})")
        lines.extend(f"- {row}" for row in rows) if rows else lines.append("-")
        lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    print(f"report -> {target}")
    return target
