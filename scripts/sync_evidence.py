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

#: The second generated section: which pages have a real-machine receipt, with
#: the receipt named. The prose used to say "live 3" long after it was 7.
LIVE_START = "<!-- live:start -->"
LIVE_END = "<!-- live:end -->"

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


def receipts_dir():
    """Where this machine keeps receipts, falling back to the repo's own path."""
    from ogtool import evidence
    from ogtool.config import Settings

    config = ROOT / "config.yaml"
    settings = Settings.load(config) if config.exists() else None
    return (evidence.receipts_dir(settings) if settings is not None
            else ROOT / "data" / "logs" / "receipts")


def render_live_block() -> str:
    """The live-evidence block the note should contain, markers included."""
    from ogtool import evidence

    directory = receipts_dir()
    rows = evidence.collect(ROOT, directory)
    return f"{LIVE_START}\n{evidence.live_markdown(rows, directory)}\n{LIVE_END}"


def replace_block(text: str, block: str,
                  start_mark: str = START,
                  end_mark: str = END) -> tuple[str, bool]:
    """Swap one marked block; returns (text, found).

    The markers are parameters on purpose: the first version looked up the
    module-level START/END no matter which block it was asked to write, so the
    live table landed between the evidence markers and the evidence table was
    destroyed - twice, before the check learned to notice.
    """
    start = text.find(start_mark)
    end = text.find(end_mark)
    if start < 0 or end < 0 or end < start:
        return text, False
    return text[:start] + block + text[end + len(end_mark):], True


def bump_current() -> int:
    """Point every note's ``current`` marker at the count pytest collects.

    The blocks are generated, but the marker above them used to be a hand
    edit, and a hand-edited number is exactly the kind that drifts: every
    test that lands made yesterday's count stale and the docs check caught
    it one run later. The note carries the number only because the check
    re-derives it, so the generator that rewrites the blocks derives it.
    """
    from ogtool.selftest import CURRENT, DOC_PATHS, collected_tests

    count, failures = collected_tests()
    for failure in failures:
        print(failure)
    if failures:
        return 1
    for path in DOC_PATHS:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        bumped = CURRENT.sub(f"<!-- current: pytest → {count} passed -->", text)
        if bumped != text:
            path.write_text(bumped, encoding="utf-8")
            print(f"{path.name}: current marker -> {count} passed")
    return count


def main(argv: list[str]) -> int:
    args = [item for item in argv[1:] if not item.startswith("--")]
    check_only = "--check" in argv
    note = Path(args[0]) if args else DEFAULT_NOTE
    if not note.exists():
        print(f"no such note: {note}")
        return 2
    current = note.read_text(encoding="utf-8")
    updated = current
    written = 0
    for start, end, render in ((START, END, lambda: render_block(note)),
                               (LIVE_START, LIVE_END, render_live_block)):
        block = render()
        updated, found = replace_block(updated, block, start, end)
        if not found:
            print(f"{note.name}: no {start} / {end} markers")
            return 2
        written += block.count(chr(10)) + 1
    if check_only:
        if updated == current:
            print(f"{note.name}: both blocks are current")
            return 0
        print(f"{note.name}: a generated block is stale")
        return 1
    note.write_text(updated, encoding="utf-8")
    print(f"{note.name}: 2 block(s) written ({written} lines)")
    bump_current()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
