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
    (tmp_path / "watch_chat-1.json").write_text(json.dumps({"mode": "live"}))
    (tmp_path / "watch_chat-2.json").write_text(json.dumps({"mode": "dry-run"}))
    (tmp_path / "watch_chat-3.json").write_text(json.dumps({"mode": "live"}))
    (tmp_path / "watch_chat-4.json").write_text("not json")
    assert evidence.count_receipts(tmp_path, "watch_chat") == (2, 1)


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
