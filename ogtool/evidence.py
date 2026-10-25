"""How much evidence each of the 25 features actually has.

The parity table says "matched" for every item, and that word hides a range: one
feature has been run against Telegram, another has only been read by a sweep, a
third has unit tests and nothing else. This module makes the range visible as a
number per feature, so "audited" stops being a claim about the document and
becomes a claim about the tree.

Levels, strongest first:

- ``live``  at least one receipt from a real run (mode live);
- ``dry``   at least one dry-run receipt (the sweep leaves these, in its own
  throwaway directory, so the project's receipts are the honest source);
- ``unit``  a test module exists, even if nothing has ever been executed;
- ``none``  nothing at all - and that is the only level that fails.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LEVELS = ("live", "dry", "unit", "none")


@dataclass(frozen=True)
class Evidence:
    feature: str
    tests: int
    live: int
    dry: int

    @property
    def level(self) -> str:
        if self.live:
            return "live"
        if self.dry:
            return "dry"
        if self.tests:
            return "unit"
        return "none"


def count_tests(root: Path, feature: str) -> int:
    """Test functions in every module whose name or text is about this feature.

    A page may be tested from more than one file (``test_settings_page.py``,
    ``test_settings_global.py``, ``test_settings_debug_gate.py``), and a name
    match alone would miss them; a text match alone would count a file that only
    mentions the word once in a comment hard to tell apart from real coverage.
    Both are used, and the count is capped at the number of test functions that
    actually exist in those files.
    """
    directory = root / "tests"
    if not directory.exists():
        return 0
    total = 0
    for path in sorted(directory.glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        named = path.name == f"test_{feature}.py" or path.name.startswith(
            f"test_{feature}_")
        mentioned = (f"features.{feature}" in text
                     or f"features import {feature}" in text)
        if not (named or mentioned):
            continue
        total += sum(1 for line in text.splitlines()
                     if line.lstrip().startswith("def test_"))
    return total


def count_receipts(directory: Path, feature: str) -> tuple[int, int]:
    """(live, dry) receipt counts for one feature."""
    live = dry = 0
    if not directory.exists():
        return 0, 0
    for path in directory.glob(f"{feature}-*.json"):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if body.get("mode") == "live":
            live += 1
        elif body.get("mode") == "dry-run":
            dry += 1
    return live, dry


def receipts_dir(settings: Any) -> Path:
    """Where this install writes receipts (same rule the writer uses)."""
    return Path(settings.log_dir) / "receipts"


def sweep_features(path: Path) -> dict[str, str]:
    """Feature -> mode from the sweep's summary, if it has run."""
    if not path.exists():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {name: str(item.get("mode", ""))
            for name, item in (body.get("features") or {}).items()}


def collect(root: Path, directory: Path,
            features: list[str] | None = None) -> list[Evidence]:
    """One row per feature, in registry order."""
    if features is None:
        import sys

        sys.path.insert(0, str(root))
        from ogtool.features import load_defaults, names

        load_defaults()
        features = names()
    swept = sweep_features(root / "logs" / "sweep.json")
    rows: list[Evidence] = []
    for feature in features:
        live, dry = count_receipts(directory, feature)
        if dry == 0 and swept.get(feature):
            # The sweep's receipts are written into throwaway directories, so
            # its summary file is the only place that remembers they happened.
            dry = 1
        rows.append(Evidence(feature, count_tests(root, feature), live, dry))
    return rows


def render(rows: list[Evidence]) -> str:
    """The table, plus the one summary line the checks parse."""
    width = max((len(row.feature) for row in rows), default=7)
    lines = [f"{'feature':{width}s}  {'level':5s}  tests  live  dry"]
    for row in rows:
        lines.append(f"{row.feature:{width}s}  {row.level:5s}  "
                     f"{row.tests:5d}  {row.live:4d}  {row.dry:3d}")
    counts = {level: sum(1 for row in rows if row.level == level)
              for level in LEVELS}
    lines.append(f"evidence: {len(rows)} feature(s), "
                 + ", ".join(f"{level} {counts[level]}" for level in LEVELS))
    return "\n".join(lines)


def problems(rows: list[Evidence]) -> list[str]:
    """Only the empty rows are failures; every other level is a fact."""
    return [f"{row.feature}: no tests and no receipts" for row in rows
            if row.level == "none"]
