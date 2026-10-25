"""One JSON receipt per run, written where every feature already passes.

Why here: ``close_task`` is the single place all 25 features say "I am done" with
the numbers they stand behind. A receipt written there covers every page without
touching any of them, and it cannot drift from the task table because it is read
straight back out of it.

Why at all: a printed summary is for the person watching; a receipt is for the
next program. The 25-item sweep can only claim "every feature ran" if each run
left something machine-readable behind, otherwise "it printed 25 lines" is the
whole evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VERSION = 1

#: Keys a reader may rely on. ``results`` is measured from the results table,
#: ``unaccounted`` is arithmetic, and both are here because they answer different
#: questions: what the store recorded, and what the counters did not cover.
REQUIRED: tuple[str, ...] = (
    "version", "feature", "task", "task_id", "mode",
    "planned", "ok", "failed", "unaccounted",
    "results", "knobs", "waited", "verdict", "created_at", "finished_at",
)

#: Longest string kept verbatim in ``knobs``; a prompt or a file body is not a
#: knob and must not turn a receipt into a copy of the task file.
KNOB_TEXT = 120


def knobs_of(raw: Any) -> tuple[dict[str, Any], int]:
    """The config a run actually stored, flattened to what a reader can use.

    Every page hands ``open_task`` a small dict of the settings it is working
    with ("file", "entries", "sources", ...). That dict is the honest answer to
    "which switches was this run using" - unlike the task file, which also holds
    values the page ignored. Long text and nested structures are dropped and
    counted, not silently truncated to look complete.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}, 1
    if not isinstance(raw, dict):
        return {}, 1
    kept: dict[str, Any] = {}
    skipped = 0
    for key, value in raw.items():
        if isinstance(value, (bool, int, float)):
            kept[str(key)] = value
        elif isinstance(value, str):
            kept[str(key)] = value if len(value) <= KNOB_TEXT \
                else value[:KNOB_TEXT] + "..."
        elif isinstance(value, (list, tuple)) and len(value) <= 5 and all(
                isinstance(item, (bool, int, float, str)) for item in value):
            kept[str(key)] = list(value)
        else:
            skipped += 1
    return kept, skipped

VERDICTS = ("dry-run", "done", "stopped", "failed")


def receipt_dir(settings: Any) -> Path:
    """Where receipts live: next to the logs the task already writes."""
    return Path(getattr(settings, "log_dir", Path("logs"))) / "receipts"


def snapshot(settings: Any) -> frozenset[str]:
    """The receipts already on disk, so a caller can tell whether one appeared."""
    directory = receipt_dir(settings)
    if not directory.exists():
        return frozenset()
    return frozenset(path.name for path in directory.glob("*.json"))


def build(store: Any, task: Any, *, ok: int, failed: int,
          waited: float = 0.0) -> dict[str, Any]:
    """The receipt body for one finished task, read back from the store."""
    row = store.task_row(task.id)
    planned = int(row["planned"] or 0) if row is not None else 0
    mode = str(row["mode"] or "") if row is not None else ""
    status = str(row["status"] or "") if row is not None else ""
    results: dict[str, int] = {"planned": 0, "sent": 0, "failed": 0}
    for result in store.results_for(task.id):
        state = str(result["status"] or "")
        if state in results:
            results[state] += 1
    knobs, dropped = knobs_of(row["config"] if row is not None else None)
    verdict = "dry-run" if mode == "dry-run" and not failed else (
        "failed" if failed else ("stopped" if status == "stopped" else "done"))
    return {
        "version": VERSION,
        "feature": task.feature,
        "task": task.name,
        "task_id": int(task.id),
        "mode": mode,
        "planned": planned,
        "ok": int(ok),
        "failed": int(failed),
        # Planned minus what the run accounted for. Not called "skipped": each
        # page has its own idea of a skip (a blank first name, a filter miss, a
        # successful candidate that was not needed), so one number cannot name
        # them all - it can only say how much of the plan went unexplained.
        "unaccounted": planned - int(ok) - int(failed),
        "results": results,
        # What the run was told to do. ``knobs_dropped`` counts the values too
        # large to be a switch (a message list, a whole file body): a receipt
        # that quietly copied them would stop being a summary.
        "knobs": knobs,
        "knobs_dropped": dropped,
        # The pacing this run would have kept (dry runs do not sleep it), summed
        # by ``pause`` in one place rather than by each page's own counter.
        "waited": round(float(waited), 1),
        "verdict": verdict,
        "created_at": str(row["created_at"] or "") if row is not None else "",
        "finished_at": str(row["finished_at"] or "") if row is not None else "",
    }


def write(settings: Any, store: Any, task: Any, *, ok: int, failed: int,
          waited: float = 0.0) -> Path | None:
    """Write the receipt; a failure to write one never breaks the run."""
    try:
        body = build(store, task, ok=ok, failed=failed, waited=waited)
        target = receipt_dir(settings)
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{body['feature']}-{body['task_id']}.json"
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        return path
    except Exception as error:  # noqa: BLE001 - a receipt is not worth a run
        print(f"receipt: not written ({type(error).__name__}: {error})")
        return None


def write_run(settings: Any, feature: str, *, exit_code: int,
              live: bool) -> Path | None:
    """A receipt for a page that never opened a task.

    ``settings``, ``proxy`` and friends edit configuration instead of working
    through a queue, so there is no task to close - but the sweep still has to be
    able to see that they ran and how they ended.
    """
    from datetime import UTC, datetime

    stamp = datetime.now(UTC).replace(microsecond=0).isoformat()
    body = {
        "version": VERSION,
        "feature": feature,
        "task": feature,
        "task_id": 0,
        "mode": "live" if live else "dry-run",
        "planned": 0,
        "ok": 0,
        "failed": 0 if exit_code == 0 else 1,
        "unaccounted": 0,
        "results": {"planned": 0, "sent": 0, "failed": 0},
        "knobs": {"live": live},
        "knobs_dropped": 0,
        "waited": 0.0,
        "verdict": ("failed" if exit_code else
                    ("dry-run" if not live else "done")),
        "created_at": stamp,
        "finished_at": stamp,
    }
    try:
        target = receipt_dir(settings)
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{feature}-run.json"
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        return path
    except Exception as error:  # noqa: BLE001 - a receipt is not worth a run
        print(f"receipt: not written ({type(error).__name__}: {error})")
        return None


def order(body: dict[str, Any]) -> tuple[str, int]:
    """Sort key: when it finished, then which task it was."""
    return (str(body.get("finished_at") or ""), int(body.get("task_id") or 0))


def line(body: dict[str, Any]) -> str:
    """One receipt as one readable line, the way the sweep prints features."""
    verdict = str(body.get("verdict") or "?")
    return (f"{str(body.get('finished_at') or '')[:19]}  "
            f"{str(body.get('feature') or '?'):16s} "
            f"{str(body.get('mode') or '?'):8s} "
            f"planned {int(body.get('planned') or 0):4d}  "
            f"ok {int(body.get('ok') or 0):4d}  "
            f"failed {int(body.get('failed') or 0):4d}  "
            f"waited {float(body.get('waited') or 0):7.1f}s  {verdict}")


#: The receipt fields a diff compares besides the knobs.
COMPARED = ("mode", "planned", "ok", "failed", "unaccounted", "verdict")


def diff(before: dict[str, Any], after: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """What changed between two receipts of the same page.

    Only the fields that describe *how* a run was configured and *what* it
    accounted for: comparing timestamps or task ids would bury the answer in
    noise.
    """
    rows: list[tuple[str, Any, Any]] = []
    for key in COMPARED:
        if before.get(key) != after.get(key):
            rows.append((key, before.get(key), after.get(key)))
    left = before.get("knobs") if isinstance(before.get("knobs"), dict) else {}
    right = after.get("knobs") if isinstance(after.get("knobs"), dict) else {}
    for key in sorted(set(left) | set(right)):
        if left.get(key) != right.get(key):
            rows.append((f"knobs.{key}", left.get(key), right.get(key)))
    if before.get("results") != after.get("results"):
        rows.append(("results", before.get("results"), after.get("results")))
    return rows


def validate(body: Any) -> list[str]:
    """Everything wrong with one receipt body, as readable sentences."""
    if not isinstance(body, dict):
        return [f"not an object: {type(body).__name__}"]
    problems = [f"missing {key}" for key in REQUIRED if key not in body]
    if body.get("version") != VERSION:
        problems.append(f"version {body.get('version')!r} != {VERSION}")
    for key in ("task_id", "planned", "ok", "failed"):
        value = body.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            problems.append(f"{key} is not an integer: {value!r}")
        elif value < 0:
            problems.append(f"{key} is negative: {value}")
    knobs = body.get("knobs")
    if not isinstance(knobs, dict):
        problems.append(f"knobs is not an object: {knobs!r}")
    dropped = body.get("knobs_dropped", 0)
    if not isinstance(dropped, int) or isinstance(dropped, bool) or dropped < 0:
        problems.append(f"knobs_dropped is not a count: {dropped!r}")
    results = body.get("results")
    if not isinstance(results, dict):
        problems.append(f"results is not an object: {results!r}")
    else:
        for key in ("planned", "sent", "failed"):
            value = results.get(key)
            if not isinstance(value, int) or isinstance(value, bool):
                problems.append(f"results.{key} is not an integer: {value!r}")
            elif value < 0:
                problems.append(f"results.{key} is negative: {value}")
    waited = body.get("waited")
    if not isinstance(waited, (int, float)) or isinstance(waited, bool) or waited < 0:
        problems.append(f"waited is not a duration: {waited!r}")
    if body.get("verdict") not in VERDICTS:
        problems.append(f"verdict {body.get('verdict')!r} not in {VERDICTS}")
    for key in ("feature", "task", "mode", "created_at", "finished_at"):
        if key in body and not isinstance(body[key], str):
            problems.append(f"{key} is not a string: {body[key]!r}")
    if not body.get("feature"):
        problems.append("feature is empty")
    return problems


def read_all(directory: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Every receipt under ``directory``, plus what could not be parsed."""
    bodies: list[dict[str, Any]] = []
    problems: list[str] = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            bodies.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as error:
            problems.append(f"{path.name}: {type(error).__name__}: {error}")
    return bodies, problems


__all__ = ["REQUIRED", "VERSION", "VERDICTS", "build", "read_all",
           "COMPARED", "KNOB_TEXT", "diff", "knobs_of", "line", "order",
           "receipt_dir",
           "snapshot", "validate", "write", "write_run"]
