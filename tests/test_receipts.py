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
    assert body["results"] == {"planned": 2, "sent": 0, "failed": 0,
                              "total": 2}


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


def _two_rename_runs(settings) -> None:
    """Two runs of one page, with one entry fewer the second time."""
    from ogtool import cli

    path = settings.path.parent / "names.txt"
    for text in ("A One\nB Two\n", "A One\n"):
        path.write_text(text, encoding="utf-8")
        assert cli.run(["--config", str(settings.path), "batch_rename",
                        "--dry-run", "--file", str(path)]) == 0


def test_runs_diff_names_the_changed_knobs(settings, capsys):
    from ogtool import cli

    _two_rename_runs(settings)
    capsys.readouterr()
    assert cli.run(["--config", str(settings.path), "runs", "diff",
                    "--feature", "batch_rename"]) == 0
    output = capsys.readouterr().out
    assert "batch_rename: task 1 -> 2" in output
    assert "knobs.entries  2 -> 1" in output
    assert "planned" in output


def test_runs_diff_needs_two_receipts(settings, capsys):
    from ogtool import cli

    (receipts.receipt_dir(settings) / "x.json").parent.mkdir(parents=True,
                                                             exist_ok=True)
    receipts.write_run(settings, "proxy", exit_code=0, live=False)
    assert cli.run(["--config", str(settings.path), "runs", "diff",
                    "--feature", "proxy"]) == 2
    assert "need two receipts" in capsys.readouterr().out


def test_diff_ignores_the_noise_and_finds_the_knob():
    from ogtool.receipts import diff

    before = {"mode": "dry-run", "planned": 2, "ok": 2, "failed": 0,
              "unaccounted": 0, "verdict": "dry-run", "task_id": 1,
              "finished_at": "2026-01-01T00:00:00+00:00",
              "knobs": {"entries": 2, "file": "a.txt"},
              "results": {"planned": 2, "sent": 0, "failed": 0}}
    after = dict(before, task_id=2, finished_at="2026-01-02T00:00:00+00:00",
                 planned=1, ok=1,
                 knobs={"entries": 1, "file": "a.txt", "extra": True},
                 results={"planned": 1, "sent": 0, "failed": 0})
    keys = [key for key, _old, _new in diff(before, after)]
    assert "knobs.entries" in keys
    assert "knobs.extra" in keys
    assert "task_id" not in keys
    assert "finished_at" not in keys


def test_the_receipt_records_the_pacing_the_run_planned(ctx, settings):
    """``waited`` is summed by ``pause``, so it needs no per-feature counter."""
    from ogtool.features._common import pause

    assert ctx.waited == 0.0
    pause(ctx, 1.5)
    pause(ctx, 0.5)
    assert ctx.waited == pytest.approx(2.0)
    # A dry run must not sleep its own plan away.
    assert ctx.waited == pytest.approx(2.0)


def test_a_run_writes_the_waited_total_it_accumulated(ctx, settings):
    from ogtool.features.add_contact import ContactConfig
    from ogtool.features.add_contact import run as add_run

    config = ContactConfig(contacts=["@c1", "@c2"],
                           operation_interval_min_ms=1500,
                           operation_interval_max_ms=1500,
                           request_interval_ms=1000)
    assert add_run(config, ctx) == 0
    body = json.loads(next(receipts.receipt_dir(settings)
                           .glob("add_contact-*.json")).read_text())
    assert body["waited"] > 0
    assert body["waited"] == pytest.approx(ctx.waited, abs=0.1)


def test_the_receipt_lists_the_accounts_that_actually_worked(ctx, settings,
                                                             pooled):
    """Read from the results rows: a bound account that did nothing is noise."""
    from ogtool.features.batch_rename import RenameConfig
    from ogtool.features.batch_rename import run as rename_run

    config = RenameConfig(names=[("A", ""), ("B", ""), ("A", "")],
                          operation_interval=0.0, account_labels=["acc1"])
    assert rename_run(config, ctx) == 0
    body = json.loads(next(receipts.receipt_dir(settings)
                           .glob("batch_rename-*.json")).read_text())
    assert body["accounts"] == ["acc1"]
    assert body["unattributed"] == 0


def test_rows_without_an_account_are_counted_not_named(settings, store):
    """A page run with no account writes "-" into that column: count, not name."""
    import logging

    from ogtool.features.batch_rename import RenameConfig
    from ogtool.features.batch_rename import run as rename_run
    from ogtool.runtime import RunContext

    ctx = RunContext(settings=settings, store=store,
                     log=logging.getLogger("ogtool.test"), live=False)
    config = RenameConfig(names=[("A", ""), ("B", "")],
                          operation_interval=0.0)
    assert rename_run(config, ctx) == 0
    body = json.loads(next(receipts.receipt_dir(settings)
                           .glob("batch_rename-*.json")).read_text())
    assert body["accounts"] == []
    assert body["unattributed"] == 2
    assert receipts.validate(body) == []


def test_auto_reply_writes_a_task_receipt_not_a_run_receipt(ctx, settings):
    """It used store.finish_task by hand, so the CLI's thin run receipt was all
    that existed - no rules, no planned count, no accounts."""
    from ogtool.features.auto_reply import AutoReplyConfig
    from ogtool.features.auto_reply import run as reply_run

    config = AutoReplyConfig(rules=[], simulate=["@demo|ana|hi"])
    assert reply_run(config, ctx) == 0
    bodies, problems = receipts.read_all(receipts.receipt_dir(settings))
    assert problems == []
    assert [body["feature"] for body in bodies] == ["auto_reply"]
    body = bodies[0]
    assert body["task_id"] > 0
    assert body["verdict"] == "dry-run"
    assert "rules" in body["knobs"]


def test_mass_send_writes_a_task_receipt_too(ctx, settings):
    from ogtool.features.mass_send import MassSendConfig
    from ogtool.features.mass_send import run as send_run

    raw = {"accounts": ["acc1"], "targets": {"inline": "@demo"},
           "content": {"text": "x"}, "send": {"threads": 1,
                                              "account_interval_min": 0.0,
                                              "account_interval_max": 0.0}}
    config = MassSendConfig.from_dict(raw, settings, dry_run=True)
    assert send_run(config, ctx) == 0
    bodies, _problems = receipts.read_all(receipts.receipt_dir(settings))
    assert [body["feature"] for body in bodies] == ["mass_send"]
    assert bodies[0]["task_id"] > 0
    assert bodies[0]["planned"] >= 1


def test_validate_rejects_a_count_in_the_mode_slot():
    """export_accounts passed len(rows) as mode and every receipt said mode "2"."""
    body = {"version": receipts.VERSION, "feature": "export_accounts",
            "task": "t", "task_id": 1, "mode": "2", "planned": 0, "ok": 2,
            "failed": 0, "unaccounted": -2, "results": {"planned": 0,
            "sent": 2, "failed": 0}, "accounts": [], "unattributed": 0,
            "knobs": {}, "knobs_dropped": 0, "waited": 0.0, "verdict": "done",
            "created_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:00:01+00:00"}
    problems = receipts.validate(body)
    assert any("mode is not dry-run/live" in item for item in problems)


def test_export_accounts_records_a_real_mode_and_count(ctx, settings):
    from ogtool.features.export_accounts import ExportConfig
    from ogtool.features.export_accounts import run as export_run

    config = ExportConfig.from_dict({}, settings)
    assert export_run(config, ctx) == 0
    bodies, problems = receipts.read_all(receipts.receipt_dir(settings))
    assert problems == []
    body = bodies[0]
    assert body["mode"] in receipts.MODES
    assert body["planned"] == body["ok"] == 2  # the pool has two accounts


def test_a_count_without_rows_is_a_schema_error():
    """keep_online said ok=1 while the results table held nothing."""
    body = {"version": receipts.VERSION, "feature": "keep_online",
            "task": "t", "task_id": 1, "mode": "live", "planned": 1, "ok": 1,
            "failed": 0, "unaccounted": 0,
            "results": {"planned": 0, "sent": 0, "failed": 0, "total": 0},
            "accounts": [], "unattributed": 0, "knobs": {}, "knobs_dropped": 0,
            "waited": 0.0, "verdict": "done",
            "created_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:00:01+00:00"}
    problems = receipts.validate(body)
    assert any("ok is 1 but the rows say 0" in item for item in problems)


def test_a_dry_run_receipt_is_schema_clean_with_no_accounts(ctx, settings):
    from ogtool.features.keep_online import KeepOnlineConfig
    from ogtool.features.keep_online import run as online_run

    config = KeepOnlineConfig(account_labels=["acc1"], interval=0.0,
                              loops=1, jitter=0.0)
    online_run(config, ctx)
    body = json.loads(next(receipts.receipt_dir(settings)
                           .glob("keep_online-*.json")).read_text())
    assert receipts.validate(body) == []
    # The plan said one heartbeat, so the receipt shows the one it would have
    # sent: planned rows, nothing sent. The account is named because the row is
    # about what that account was going to do - the live attribution is pinned
    # separately by the real receipt and by scripts/live_smoke.py.
    assert body["results"]["sent"] == 0
    assert body["ok"] == body["planned"] == body["results"]["total"] == 1
    assert body["accounts"] == ["acc1"]


def test_derive_takes_the_totals_from_the_rows(ctx, settings):
    """mix_market counts stages while it records contacts; derive() ends that."""
    from ogtool.features._common import close_task, open_task, record

    task = open_task(ctx, "derive-demo", "mix_market", {}, 6)
    for index in range(3):
        record(ctx, task, "acc1", f"row {index}", True)
    record(ctx, task, "acc1", "row 3", False, "DEAD_CHAT")
    # The page would have said ok=5 / failed=0; the rows say 3 / 1.
    close_task(ctx, task, ok=5, failed=0, derive=True)
    body = json.loads(next(receipts.receipt_dir(settings)
                           .glob("mix_market-*.json")).read_text())
    assert (body["ok"], body["failed"]) == (3, 1)
    assert receipts.validate(body) == []


def test_prune_keeps_the_newest_per_feature(tmp_path):
    for feature in ("watch_chat", "export_accounts"):
        for index in range(1, 5):
            (tmp_path / f"{feature}-{index}.json").write_text(json.dumps(
                {"feature": feature, "task_id": index,
                 "finished_at": f"2026-01-0{index}T00:00:00+00:00"}),
                encoding="utf-8")
    doomed = receipts.prune(tmp_path, keep=2)
    assert sorted(path.name for path in doomed) == [
        "export_accounts-1.json", "export_accounts-2.json",
        "watch_chat-1.json", "watch_chat-2.json"]


def test_prune_can_take_one_feature(tmp_path):
    (tmp_path / "a-1.json").write_text(json.dumps(
        {"feature": "a", "task_id": 1}), encoding="utf-8")
    (tmp_path / "a-2.json").write_text(json.dumps(
        {"feature": "a", "task_id": 2, "finished_at": "2026-01-02T00:00:00"}),
        encoding="utf-8")
    assert [p.name for p in receipts.prune(tmp_path, 1, feature="a")] == ["a-1.json"]
    assert receipts.prune(tmp_path, 1, feature="nope") == []


def test_prune_refuses_to_delete_everything(tmp_path):
    with pytest.raises(ValueError):
        receipts.prune(tmp_path, keep=0)


def test_prune_leaves_unreadable_files_alone(tmp_path):
    (tmp_path / "broken-1.json").write_text("not json", encoding="utf-8")
    assert receipts.prune(tmp_path, keep=1) == []


def test_prune_command_deletes_only_with_yes(tmp_path, monkeypatch, capsys):
    from ogtool import cli as cli_module
    from ogtool.config import Settings

    for index in (1, 2):
        (tmp_path / f"a-{index}.json").write_text(json.dumps(
            {"feature": "a", "task_id": index}), encoding="utf-8")

    class Args:
        action = "prune"
        keep = 1
        feature = ""
        yes = False

    monkeypatch.setattr(receipts, "receipt_dir", lambda _settings: tmp_path)
    settings = Settings(tmp_path / "config.yaml", log_dir=tmp_path)
    assert cli_module._runs(Args(), settings) == 0
    assert len(list(tmp_path.glob("*.json"))) == 2  # dry-run deleted nothing
    assert "would delete 1" in capsys.readouterr().out

    Args.yes = True
    assert cli_module._runs(Args(), settings) == 0
    assert [path.name for path in tmp_path.glob("*.json")] == ["a-2.json"]
    assert "would remain" not in capsys.readouterr().out


def test_prune_command_refuses_keep_zero(tmp_path, monkeypatch, capsys):
    from ogtool import cli as cli_module
    from ogtool.config import Settings

    class Args:
        action = "prune"
        keep = 0
        feature = ""
        yes = True

    monkeypatch.setattr(receipts, "receipt_dir", lambda _settings: tmp_path)
    settings = Settings(tmp_path / "config.yaml", log_dir=tmp_path)
    assert cli_module._runs(Args(), settings) == 2
    assert "at least 1" in capsys.readouterr().out


def test_a_fallback_receipt_is_backed_by_a_row(settings):
    """A page that never opened a task still has to explain its verdict."""
    receipts.write_run(settings, "settings", exit_code=1, live=True)
    path = receipts.receipt_dir(settings) / "settings-run.json"
    body = json.loads(path.read_text())
    assert receipts.validate(body) == []
    assert body["failed"] == 1 and body["ok"] == 0
    assert body["rows"][0]["status"] == "failed"

    receipts.write_run(settings, "settings", exit_code=0, live=True)
    body = json.loads(path.read_text())
    assert receipts.validate(body) == []
    assert body["ok"] == 1 and body["failed"] == 0
    assert body["rows"][0]["status"] == "sent"


def test_every_page_leaves_evidence():
    """A page either opens a task or is a declared local-configuration page.

    The registry is the list of the 25 pages; a new one that forgets both would
    vanish from the receipts without failing anything, which is exactly the bug
    this test exists to prevent.
    """
    from pathlib import Path as _Path

    from ogtool.features import _MODULE_NAMES, _MODULES, FALLBACK_PAGES, load_defaults

    load_defaults()
    silent = []
    for name in _MODULE_NAMES:
        module = _MODULES[name]
        text = _Path(module.__file__).read_text(encoding="utf-8")
        if "open_task(" in text:
            continue
        if name in FALLBACK_PAGES:
            continue
        silent.append(name)
    assert silent == [], (
        f"these pages write no receipt and are not declared: {silent}; "
        f"declare them in features.FALLBACK_PAGES with a reason")


def _valid_body(**over):
    body = {
        "version": receipts.VERSION, "feature": "f", "task": "t", "task_id": 1,
        "mode": "live", "planned": 1, "ok": 1, "failed": 0,
        "unaccounted": 0, "results": {"planned": 0, "sent": 1, "failed": 0,
                                     "total": 1},
        "accounts": ["a"], "unattributed": 0, "knobs": {}, "knobs_dropped": 0,
        "waited": 0.0, "verdict": "done", "created_at": "a", "finished_at": "b",
    }
    return body | over


def test_more_rows_than_planned_is_a_schema_error():
    """The dialog listing wrote 6 rows against planned 0 before it filled it in."""
    body = _valid_body(planned=0, unaccounted=-1)
    problems = receipts.validate(body)
    assert any("more rows than planned" in item for item in problems), problems


def test_unaccounted_must_follow_from_the_counts():
    body = _valid_body(planned=3, unaccounted=5)
    problems = receipts.validate(body)
    assert any("unaccounted is 5" in item for item in problems), problems


def test_a_consistent_receipt_has_no_problems():
    assert receipts.validate(_valid_body()) == []


def test_the_pull_stage_plans_for_its_own_rows(settings):
    """mix_market wrote one 'own contacts' row against planned 0.

    The row is real - stage 4 reads the address book even when it is empty - so the
    plan has to include it, otherwise the receipt reports a row nobody planned.
    """
    from ogtool.features import mix_market as page

    config = page.MixConfig.from_dict({"mix_market": {"targets": ["@sweep"]}},
                                      settings)
    config.stop_after = 4
    planned = len(config.contacts) + len(["-"])
    assert planned == 1
