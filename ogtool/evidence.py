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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LEVELS = ("live", "dry", "unit", "none")


@dataclass(frozen=True)
class Evidence:
    feature: str
    tests: int
    live: int
    dry: int
    #: When the newest live receipt finished, ISO 8601, or "" for never. A count
    #: says a page was proven once; the date says whether that proof is old
    #: enough to be worth re-running.
    last_live: str = ""
    #: Live receipts that were NOT counted as proof, and why the newest one lost.
    #: Without this a page looks like it was never run on a real account when in
    #: fact it was run and recorded nothing - two very different problems.
    rejected: int = 0
    why: str = ""

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


#: Failure codes that mean the run never reached Telegram: a missing or busy
#: session, missing api credentials. A receipt of only these proves the command
#: was invoked, not that anything real was touched.
LOCAL_CODES = {"SESSION_BUSY", "SESSION_NOT_FOUND", "API_CREDENTIALS", "NO_ACCOUNT"}


def _has_no_rows(body: dict[str, Any]) -> bool:
    """A live run that recorded nothing proves it connected, not that it worked.

    watch_chat and auto_reply sat at planned 0 for weeks: the session opened, no
    message arrived, nothing was recorded - and the empty receipt was counted as
    live evidence because only "all rows are local failures" was excluded. A
    receipt with no rows at all cannot support the same claim as one with rows.
    """
    results = body.get("results")
    if isinstance(results, dict) and int(results.get("total") or 0) > 0:
        return False
    return not body.get("rows")


def _only_local_failures(body: dict[str, Any]) -> bool:
    """Did this run fail for reasons that never reached Telegram?

    Task receipts keep no rows (only counts), so the codes dict is what answers
    this: without it a live run that only ever hit SESSION_BUSY counted as
    real-machine evidence, because the row-wise check below never saw a row.
    """
    codes = body.get("codes")
    if isinstance(codes, dict) and codes:
        results = body.get("results") or {}
        if int(results.get("total") or 0) <= int(results.get("failed") or 0):
            return set(codes) <= LOCAL_CODES
    rows = body.get("rows") or []
    if not rows:
        return False
    for row in rows:
        if not isinstance(row, dict):
            return False
        if row.get("status") != "failed":
            return False
        if str(row.get("code") or "") not in LOCAL_CODES:
            return False
    return True


def count_receipts(directory: Path, feature: str) -> tuple[int, int, str]:
    """(live, dry, newest live finish) for one feature."""
    live, dry, newest, _, _ = count_receipts_full(directory, feature)
    return live, dry, newest


def count_receipts_full(directory: Path, feature: str) -> tuple[
        int, int, str, int, str]:
    """The same counts, plus how many live runs were rejected and why.

    The why is read from the newest rejected receipt, so the note can say
    "recorded no rows" or "SESSION_BUSY 2" instead of leaving a dry grade
    unexplained.
    """
    live = dry = 0
    rejected = 0
    why = ""
    newest = ""
    if not directory.exists():
        return 0, 0, newest, 0, ""
    for path in directory.glob(f"{feature}-*.json"):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if body.get("mode") == "live":
            # A fallback receipt (task_id 0) only says the command ran: it has
            # one synthetic row about the run, not a row per chat or account.
            # Counting it as live evidence would let 'settings' claim real-machine
            # proof for reading a config file.
            task_id = int(body.get("task_id") or 0)
            if task_id <= 0:
                pass
            elif _has_no_rows(body):
                rejected += 1
                why = "recorded no rows"
            elif _only_local_failures(body):
                rejected += 1
                codes = body.get("codes")
                why = (", ".join(str(code) for code in sorted(codes))
                       if isinstance(codes, dict) and codes else "local failure")
            else:
                live += 1
            stamp = str(body.get("finished_at") or "")
            if stamp > newest:
                newest = stamp
        elif body.get("mode") == "dry-run":
            dry += 1
    return live, dry, newest, rejected, why


def stamp_short(stamp: str) -> str:
    """A stamp as ``YYYY-MM-DD HH:MM``, for tables that must not drift.

    The age ("3h") is right for a person reading the screen and wrong for a file
    compared tomorrow: the block in the note is checked against the tree, so it
    carries the instant, not the distance to now.
    """
    if not stamp:
        return "-"
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return "?"
    return when.strftime("%Y-%m-%d %H:%M")


def age(stamp: str, now: datetime | None = None) -> str:
    """A stamp as a short age ("3h", "2d"), or "" when there is no stamp."""
    if not stamp:
        return ""
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return "?"
    moment = now or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    seconds = max(0.0, (moment - when).total_seconds())
    for limit, unit in ((60, "s"), (3600, "m"), (86400, "h")):
        if seconds < limit:
            return f"{int(seconds)}{unit}" if unit == "s" else (
                f"{int(seconds // 60)}m" if unit == "m" else f"{int(seconds // 3600)}h")
    return f"{int(seconds // 86400)}d"


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
        live, dry, newest, rejected, why = count_receipts_full(directory,
                                                              feature)
        if dry == 0 and swept.get(feature):
            # The sweep's receipts are written into throwaway directories, so
            # its summary file is the only place that remembers they happened.
            dry = 1
        rows.append(Evidence(feature, count_tests(root, feature), live, dry,
                             newest, rejected, why if not live else ""))
    return rows


def render(rows: list[Evidence]) -> str:
    """The table, plus the one summary line the checks parse."""
    width = max((len(row.feature) for row in rows), default=7)
    lines = [f"{'feature':{width}s}  {'level':5s}  tests  live  dry  last"
             f"  why"]
    for row in rows:
        lines.append(f"{row.feature:{width}s}  {row.level:5s}  "
                     f"{row.tests:5d}  {row.live:4d}  {row.dry:3d}  "
                     f"{age(row.last_live) or '-':>5s}  "
                     f"{row.why or ''}".rstrip())
    counts = {level: sum(1 for row in rows if row.level == level)
              for level in LEVELS}
    lines.append(f"evidence: {len(rows)} feature(s), "
                 + ", ".join(f"{level} {counts[level]}" for level in LEVELS))
    return "\n".join(lines)


def as_dict(rows: list[Evidence]) -> dict[str, Any]:
    """The same table as data, for whatever writes it next."""
    return {
        "features": [
            {"feature": row.feature, "level": row.level, "tests": row.tests,
             "live": row.live, "dry": row.dry, "last_live": row.last_live,
             "age": age(row.last_live), "rejected": row.rejected,
             "why": row.why}
            for row in rows
        ],
        "counts": {level: sum(1 for row in rows if row.level == level)
                   for level in LEVELS},
    }


def newest_live(directory: Path, feature: str) -> dict[str, Any] | None:
    """The newest live receipt of one feature, or None."""
    best: dict[str, Any] | None = None
    best_key = ""
    if not directory.exists():
        return None
    for path in directory.glob(f"{feature}-*.json"):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if body.get("mode") != "live" or int(body.get("task_id") or 0) <= 0:
            continue
        key = str(body.get("finished_at") or "")
        if best is None or key >= best_key:
            best, best_key = body, key
            best["_file"] = path.name
    return best


def live_markdown(rows: list[Evidence], directory: Path) -> str:
    """One row per page that has real-machine evidence, with the receipt named.

    The prose in the note used to say 'live 3' months after it stopped being
    true; this section is generated from the receipts themselves, so the claim
    and its evidence are written by the same code.
    """
    lines = ["| 项 | 最新真机回执 | 计数 | 时间 |", "|---|---|---|---|"]
    found = 0
    for row in rows:
        if row.level != "live":
            continue
        body = newest_live(directory, row.feature)
        if body is None:  # pragma: no cover - live level implies a receipt
            continue
        found += 1
        lines.append(
            f"| `{row.feature}` | `{body['_file']}` | "
            f"planned {body.get('planned', 0)} / ok {body.get('ok', 0)} / "
            f"failed {body.get('failed', 0)} | "
            f"{stamp_short(str(body.get('finished_at') or ''))} |")
    lines.append("")
    lines.append(f"共 {found} 项有真机回执；其余 {len(rows) - found} 项只有干跑"
                 f"或单测证据。")
    return "\n".join(lines)


def as_markdown(rows: list[Evidence]) -> str:
    """A paste-ready table, so the note never carries a number nobody derived."""
    lines = ["| 项 | 证据等级 | 测试 | 真机 | 干跑 | 最近真机 | 真机被排除 |",
             "|---|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| `{row.feature}` | {row.level} | {row.tests} | "
                     f"{row.live} | {row.dry} | "
                     f"{stamp_short(row.last_live)} | "
                     f"{row.rejected or ''}"
                     f"{('（' + row.why + '）') if row.why else ''} |")
    counts = as_dict(rows)["counts"]
    lines.append("")
    lines.append(f"共 {len(rows)} 项：" + "，".join(
        f"{level} {counts[level]}" for level in LEVELS))
    return "\n".join(lines)


def problems(rows: list[Evidence]) -> list[str]:
    """Only the empty rows are failures; every other level is a fact."""
    return [f"{row.feature}: no tests and no receipts" for row in rows
            if row.level == "none"]
