"""The evidence table: how much proof each feature has, and no level above what
the tree can show."""

from __future__ import annotations

import json
from pathlib import Path

from ogtool import cli, evidence


def test_a_named_test_module_counts(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_watch_chat.py").write_text(
        "def test_one():\n    pass\n\ndef test_two():\n    pass\n",
        encoding="utf-8")
    assert evidence.count_tests(tmp_path, "watch_chat") == 2


def test_a_mentioning_module_counts_too(tmp_path):
    """settings is covered from three files, none of them called test_settings.py."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_settings_global.py").write_text(
        "from ogtool.features import settings\n\ndef test_a():\n    pass\n",
        encoding="utf-8")
    assert evidence.count_tests(tmp_path, "settings") == 1


def test_an_unrelated_module_does_not_count(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_other.py").write_text(
        "def test_a():\n    pass\n", encoding="utf-8")
    assert evidence.count_tests(tmp_path, "settings") == 0


def test_receipts_are_counted_by_mode(tmp_path):
    (tmp_path / "watch_chat-1.json").write_text(
        json.dumps({"mode": "live", "task_id": 1}))
    (tmp_path / "watch_chat-2.json").write_text(json.dumps({"mode": "dry-run"}))
    (tmp_path / "watch_chat-3.json").write_text(
        json.dumps({"mode": "live", "task_id": 3}))
    (tmp_path / "watch_chat-4.json").write_text("not json")
    live, dry, newest = evidence.count_receipts(tmp_path, "watch_chat")
    assert (live, dry) == (2, 1)
    assert newest == ""  # no finished_at in these bodies


def test_levels_fall_back_in_order():
    assert evidence.Evidence("f", 0, 1, 0).level == "live"
    assert evidence.Evidence("f", 0, 0, 1).level == "dry"
    assert evidence.Evidence("f", 3, 0, 0).level == "unit"
    assert evidence.Evidence("f", 0, 0, 0).level == "none"


def test_only_the_empty_row_is_a_problem():
    rows = [evidence.Evidence("a", 1, 0, 0), evidence.Evidence("b", 0, 0, 0)]
    problems = evidence.problems(rows)
    assert problems == ["b: no tests and no receipts"]


def test_the_sweep_summary_supplies_dry_evidence(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "sweep.json").write_text(json.dumps(
        {"features": {"batch_rename": {"mode": "dry-run"}}}), encoding="utf-8")
    (tmp_path / "tests").mkdir()
    rows = evidence.collect(tmp_path, tmp_path / "nope",
                            features=["batch_rename", "proxy"])
    assert rows[0].level == "dry"
    assert rows[1].level == "none"


def test_the_table_names_every_level(tmp_path):
    rows = [evidence.Evidence("a", 1, 0, 0), evidence.Evidence("b", 0, 2, 0)]
    text = evidence.render(rows)
    assert "level" in text
    assert text.splitlines()[-1].startswith("evidence: 2 feature(s)")


def test_the_evidence_command_exits_zero_when_nothing_is_empty(settings,
                                                               monkeypatch):
    monkeypatch.setattr(evidence, "collect", lambda *a, **k: [
        evidence.Evidence("a", 1, 0, 0)])
    assert cli._evidence(settings) == 0


def test_the_evidence_command_fails_on_an_empty_feature(settings, monkeypatch):
    monkeypatch.setattr(evidence, "collect", lambda *a, **k: [
        evidence.Evidence("a", 0, 0, 0)])
    assert cli._evidence(settings) == 1


def test_receipts_dir_follows_the_settings(settings):
    assert evidence.receipts_dir(settings) == Path(settings.log_dir) / "receipts"


def test_json_output_is_the_same_rows():
    rows = [evidence.Evidence("a", 2, 1, 0), evidence.Evidence("b", 0, 0, 1)]
    body = evidence.as_dict(rows)
    assert body["features"][0] == {"feature": "a", "level": "live",
                                   "tests": 2, "live": 1, "dry": 0,
                                   "last_live": "", "age": ""}
    assert body["counts"]["live"] == 1
    assert body["counts"]["dry"] == 1


def test_markdown_names_every_row_and_the_totals():
    rows = [evidence.Evidence("a", 2, 0, 1), evidence.Evidence("b", 3, 0, 0)]
    text = evidence.as_markdown(rows)
    assert text.startswith("| 项 | 证据等级 |")
    assert "| `a` | dry | 2 | 0 | 1 |" in text
    assert text.strip().endswith("共 2 项：live 0，dry 1，unit 1，none 0")


def test_the_two_formats_agree(settings, monkeypatch, capsys):
    rows = [evidence.Evidence("a", 1, 0, 0)]
    monkeypatch.setattr(evidence, "collect", lambda *a, **k: rows)
    assert cli._evidence(settings, as_json=True) == 0
    as_json = capsys.readouterr().out
    assert cli._evidence(settings, as_markdown=True) == 0
    as_md = capsys.readouterr().out
    assert '"feature": "a"' in as_json
    assert "| `a` | unit |" in as_md


def test_age_reads_like_a_person_wrote_it():
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    assert evidence.age("", now) == ""
    assert evidence.age("not a stamp", now) == "?"
    assert evidence.age(
        (now - timedelta(seconds=30)).isoformat(), now) == "30s"
    assert evidence.age((now - timedelta(minutes=5)).isoformat(), now) == "5m"
    assert evidence.age((now - timedelta(hours=3)).isoformat(), now) == "3h"
    assert evidence.age((now - timedelta(days=2)).isoformat(), now) == "2d"


def test_the_newest_live_receipt_is_remembered(tmp_path):
    (tmp_path / "watch_chat-1.json").write_text(json.dumps(
        {"mode": "live", "finished_at": "2026-01-01T00:00:00+00:00"}))
    (tmp_path / "watch_chat-2.json").write_text(json.dumps(
        {"mode": "live", "finished_at": "2026-01-03T00:00:00+00:00"}))
    (tmp_path / "watch_chat-3.json").write_text(json.dumps(
        {"mode": "dry-run", "finished_at": "2026-01-09T00:00:00+00:00"}))
    _live, _dry, newest = evidence.count_receipts(tmp_path, "watch_chat")
    assert newest == "2026-01-03T00:00:00+00:00"


def test_the_table_shows_an_age_and_the_markdown_an_instant():
    """A file compared tomorrow must not depend on when it was written."""
    row = evidence.Evidence("a", 1, 2, 0, "2026-01-01T00:00:00+00:00")
    assert "last" in evidence.render([row]).splitlines()[0]
    markdown = evidence.as_markdown([row])
    assert "最近真机" in markdown
    assert "2026-01-01 00:00" in markdown
    assert evidence.stamp_short("") == "-"


def test_a_fallback_receipt_is_not_live_proof(tmp_path):
    """'settings' editing a config file is not real-machine evidence."""
    (tmp_path / "settings-run.json").write_text(json.dumps(
        {"mode": "live", "task_id": 0, "finished_at": "2026-01-01T00:00:00"}))
    (tmp_path / "watch_chat-1.json").write_text(json.dumps(
        {"mode": "live", "task_id": 1605,
         "finished_at": "2026-01-01T00:00:00"}))
    assert evidence.count_receipts(tmp_path, "settings")[0] == 0
    assert evidence.count_receipts(tmp_path, "watch_chat")[0] == 1


def test_a_session_lock_is_not_live_proof(tmp_path):
    """SESSION_BUSY means the run never reached Telegram at all."""
    body = {"mode": "live", "task_id": 1633, "rows": [
        {"status": "failed", "code": "SESSION_BUSY", "subject": "x"}]}
    (tmp_path / "mass_send-1.json").write_text(json.dumps(body))
    assert evidence.count_receipts(tmp_path, "mass_send")[0] == 0

    body["rows"] = [{"status": "sent", "code": "", "subject": "x"}]
    (tmp_path / "mass_send-2.json").write_text(json.dumps(body))
    assert evidence.count_receipts(tmp_path, "mass_send")[0] == 1

    # A real server refusal is still real-machine evidence.
    body["rows"] = [{"status": "failed", "code": "CHANNEL_PRIVATE",
                     "subject": "x"}]
    (tmp_path / "mass_send-3.json").write_text(json.dumps(body))
    assert evidence.count_receipts(tmp_path, "mass_send")[0] == 2


def test_the_live_block_names_the_receipt(tmp_path):
    """The note must point at the receipt, not just claim a number."""
    (tmp_path / "watch_chat-1.json").write_text(json.dumps(
        {"mode": "live", "task_id": 7, "planned": 1, "ok": 1, "failed": 0,
         "finished_at": "2026-01-01T00:00:00"}))
    rows = [evidence.Evidence("watch_chat", 3, 1, 0,
                              "2026-01-01T00:00:00"),
            evidence.Evidence("auto_reply", 3, 0, 1)]
    text = evidence.live_markdown(rows, tmp_path)
    assert "watch_chat-1.json" in text
    assert "planned 1 / ok 1 / failed 0" in text
    assert "auto_reply" not in text          # dry-run only: not in this table
    assert "共 1 项有真机回执" in text


def test_a_fallback_receipt_never_reaches_the_live_table(tmp_path):
    (tmp_path / "settings-run.json").write_text(json.dumps(
        {"mode": "live", "task_id": 0, "finished_at": "2026-01-01T00:00:00"}))
    rows = [evidence.Evidence("settings", 1, 1, 0, "2026-01-01T00:00:00")]
    assert "settings-run.json" not in evidence.live_markdown(rows, tmp_path)
