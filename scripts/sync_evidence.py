"""Rewrite the evidence block in a note from the tree it describes.

The parity note carried a hand-written "matched" for every item, and the numbers
in it were typed by me. This script makes the tool write that section: the block
between the two markers is replaced by what ``ogtool evidence --markdown`` prints,
so the note cannot say more than the tree can show.

Usage::

    PYTHONPATH=. python scripts/sync_evidence.py             # the default note
    PYTHONPATH=. python scripts/sync_evidence.py --check     # exit 1 if stale
    PYTHONPATH=. python scripts/sync_evidence.py path/to/note.md
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

START = "<!-- evidence:start -->"
END = "<!-- evidence:end -->"

#: Where the note lives on this machine. The path is not in the repository, so a
#: missing file is reported, not created.
DEFAULT_NOTE = (Path.home() / ".dsh/bot/workspace/proj-tdesktop/coder"
                / "功能对齐-25项-对照表.md")


def render_block(note: Path | None = None) -> str:
    """The block the note should contain, markers included."""
    from ogtool import evidence
    from ogtool.config import Settings

    config = ROOT / "config.yaml"
    settings = Settings.load(config) if config.exists() else None
    directory = (evidence.receipts_dir(settings) if settings is not None
                 else ROOT / "data" / "logs" / "receipts")
    rows = evidence.collect(ROOT, directory)
    return f"{START}\n{evidence.as_markdown(rows)}\n{END}"


def replace_block(text: str, block: str) -> tuple[str, bool]:
    """Swap the marked block; returns (text, found)."""
    start = text.find(START)
    end = text.find(END)
    if start < 0 or end < 0 or end < start:
        return text, False
    return text[:start] + block + text[end + len(END):], True


def main(argv: list[str]) -> int:
    args = [item for item in argv[1:] if not item.startswith("--")]
    check_only = "--check" in argv
    note = Path(args[0]) if args else DEFAULT_NOTE
    if not note.exists():
        print(f"no such note: {note}")
        return 2
    current = note.read_text(encoding="utf-8")
    block = render_block(note)
    updated, found = replace_block(current, block)
    if not found:
        print(f"{note.name}: no {START} / {END} markers")
        return 2
    if check_only:
        if updated == current:
            print(f"{note.name}: evidence block is current")
            return 0
        print(f"{note.name}: evidence block is stale")
        return 1
    note.write_text(updated, encoding="utf-8")
    print(f"{note.name}: evidence block written "
          f"({block.count(chr(10)) + 1} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
