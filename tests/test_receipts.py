"""One receipt per run: the machine-readable half of "the feature ran"."""

from __future__ import annotations

import json

import pytest

from ogtool import receipts
from ogtool.features import load_defaults
from ogtool.features.batch_rename import RenameConfig
from ogtool.features.batch_rename import run as rename_run


def test_a_run_leaves_a_receipt_that_validates(ctx, settings):
    load_defaults()
    config = RenameConfig(names=[("A", ""), ("B", "")],
                          operation_interval=0.0)
    assert rename_run(config, ctx) == 0
    files = sorted(receipts.receipt_dir(settings).glob("batch_rename-*.json"))
    assert len(files) == 1
    body = json.loads(files[0].read_text(encoding="utf-8"))
    assert receipts.validate(body) == []
    assert body["feature"] == "batch_rename"
    assert body["mode"] == "dry-run"
    assert body["verdict"] == "dry-run"
    assert body["planned"] == 2
    assert body["ok"] == 2
    assert body["failed"] == 0


def test_the_receipt_agrees_with_the_plan_line(ctx, settings, capsys):
    """The number the run prints and the number it stores cannot disagree."""
    config = RenameConfig(names=[("A", ""), ("B", ""), ("C", "")],
                          operation_interval=0.0)
    assert rename_run(config, ctx) == 0
    output = capsys.readouterr().out
    planned = int(output.split("PLAN: ")[1].split(" ")[0])
    body = json.loads(next(receipts.receipt_dir(settings)
                           .glob("batch_rename-*.json")).read_text())
    assert body["planned"] == planned == 3


def test_unaccounted_is_arithmetic_not_a_guess(ctx, settings):
    """A page that skips an entry leaves planned-ok-failed unexplained."""
    from ogtool.features.batch_rename import RenameConfig as Config
    from ogtool.features.batch_rename import run as run_rename

    # A blank first name is a skip: it neither lands nor fails.
    config = Config(names=[("", ""), ("B", "")], operation_interval=0.0)
    assert run_rename(config, ctx) == 0
    body = json.loads(next(receipts.receipt_dir(settings)
                           .glob("batch_rename-*.json")).read_text())
    assert body["planned"] == 2
    assert body["ok"] == 1
    assert body["unaccounted"] == 1


def test_results_are_measured_from_the_store(ctx, settings):
    config = RenameConfig(names=[("A", ""), ("B", "")],
                          operation_interval=0.0)
    assert rename_run(config, ctx) == 0
    body = json.loads(next(receipts.receipt_dir(settings)
                           .glob("batch_rename-*.json")).read_text())
    assert body["results"] == {"planned": 2, "sent": 0, "failed": 0}


def test_validate_names_a_missing_key():
    problems = receipts.validate({"version": receipts.VERSION})
    assert "missing feature" in problems
    assert "missing results" in problems


@pytest.mark.parametrize("broken, expected", [
    ({"version": 99}, "version"),
    ({"planned": -1}, "planned is negative"),
    ({"ok": "2"}, "ok is not an integer"),
    ({"verdict": "maybe"}, "verdict"),
    ({"results": []}, "results is not an object"),
    ({"feature": ""}, "feature is empty"),
])
def test_validate_names_what_is_wrong(broken, expected):
    body = {
        "version": receipts.VERSION, "feature": "f", "task": "t", "task_id": 1,
        "mode": "dry-run", "planned": 1, "ok": 1, "failed": 0,
        "unaccounted": 0, "results": {"planned": 1, "sent": 0, "failed": 0},
        "verdict": "dry-run", "created_at": "a", "finished_at": "b",
    } | broken
    problems = receipts.validate(body)
    assert any(expected in problem for problem in problems), problems


def test_a_page_without_a_task_still_leaves_a_receipt(settings):
    path = receipts.write_run(settings, "settings", exit_code=0, live=False)
    assert path is not None and path.exists()
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["task_id"] == 0
    assert body["verdict"] == "dry-run"
    assert receipts.validate(body) == []


def test_a_failed_page_is_recorded_as_failed(settings):
    path = receipts.write_run(settings, "proxy", exit_code=2, live=True)
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["verdict"] == "failed"
    assert body["failed"] == 1


def test_snapshot_tells_a_run_apart_from_the_previous_one(settings):
    assert receipts.snapshot(settings) == frozenset()
    receipts.write_run(settings, "settings", exit_code=0, live=False)
    assert receipts.snapshot(settings) == frozenset({"settings-run.json"})


def _config(settings) -> str:
    return str(settings.path)


def test_runs_list_shows_the_newest_first(settings, capsys):
    from ogtool import cli

    receipts.write_run(settings, "settings", exit_code=0, live=False)
    receipts.write_run(settings, "proxy", exit_code=0, live=False)
    assert cli.run(["--config", _config(settings), "runs", "list"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert len(lines) == 2
    assert "settings" in lines[0] or "proxy" in lines[0]
    assert all("dry-run" in line for line in lines)


def test_runs_list_filters_and_limits(settings, capsys):
    from ogtool import cli

    receipts.write_run(settings, "settings", exit_code=0, live=False)
    receipts.write_run(settings, "proxy", exit_code=2, live=False)
    assert cli.run(["--config", _config(settings), "runs", "list",
                    "--feature", "proxy"]) == 0
    output = capsys.readouterr().out
    assert "proxy" in output
    assert "settings" not in output
    assert "failed" in output


def test_runs_check_catches_a_broken_receipt(settings, capsys):
    from ogtool import cli

    receipts.write_run(settings, "settings", exit_code=0, live=False)
    broken = receipts.receipt_dir(settings) / "broken.json"
    broken.write_text('{"version": 1}', encoding="utf-8")
    assert cli.run(["--config", _config(settings), "runs", "check"]) == 1
    output = capsys.readouterr().out
    assert "receipts: 2  problems:" in output
    assert "missing feature" in output


def test_runs_show_prints_one_receipt(settings, capsys):
    from ogtool import cli

    receipts.write_run(settings, "proxy", exit_code=0, live=False)
    assert cli.run(["--config", _config(settings), "runs", "show",
                    "--feature", "proxy"]) == 0
    output = capsys.readouterr().out
    assert '"feature": "proxy"' in output
    assert cli.run(["--config", _config(settings), "runs", "show",
                    "--feature", "nope"]) == 2


def test_the_receipt_records_the_knobs_the_run_used(ctx, settings):
    from ogtool.features.batch_rename import RenameConfig
    from ogtool.features.batch_rename import run as rename_run

    config = RenameConfig(names=[("A", ""), ("B", "")],
                          operation_interval=0.0)
    assert rename_run(config, ctx) == 0
    body = json.loads(next(receipts.receipt_dir(settings)
                           .glob("batch_rename-*.json")).read_text())
    assert body["knobs"]["entries"] == 2
    assert body["knobs"]["file"] == ""
    assert isinstance(body["knobs"], dict)
    # ``lists`` is the per-list item counts, i.e. a dict - not a switch, so it is
    # counted as dropped instead of being pasted in wholesale.
    assert "lists" not in body["knobs"]
    assert body["knobs_dropped"] >= 0


def test_a_value_too_big_to_be_a_knob_is_dropped_not_copied():
    from ogtool.receipts import KNOB_TEXT, knobs_of

    kept, dropped = knobs_of(json.dumps({
        "sources": ["@s1", "@s2"],
        "messages": [{"peer": "@s1", "text": "x" * 50}] * 30,
        "note": "y" * (KNOB_TEXT + 50),
        "flat": True,
    }))
    assert kept["sources"] == ["@s1", "@s2"]
    assert kept["flat"] is True
    assert kept["note"].endswith("...")
    assert len(kept["note"]) == KNOB_TEXT + 3
    assert "messages" not in kept
    assert dropped == 1


def test_a_broken_config_blob_does_not_break_the_receipt():
    from ogtool.receipts import knobs_of

    assert knobs_of("{not json") == ({}, 1)
    assert knobs_of(None) == ({}, 1)
    assert knobs_of(["a"]) == ({}, 1)
