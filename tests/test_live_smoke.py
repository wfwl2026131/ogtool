"""The live suite's own plumbing, tested without a login.

The cases themselves need a real account; what can be pinned offline is that the
script refuses writes, reads the receipt directory out of the config, and picks
the newest receipt instead of an arbitrary one.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def smoke():
    spec = importlib.util.spec_from_file_location(
        "og_live_smoke", ROOT / "scripts/live_smoke.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # A dataclass resolves its annotations through sys.modules, so the module has
    # to be registered before it is executed.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_list_names_every_case(smoke, capsys):
    assert smoke.main(["live_smoke", "--list"]) == 0
    out = capsys.readouterr().out
    for case in smoke.CASES:
        assert case.name in out


def test_the_cases_are_read_only_by_default(smoke):
    """Nothing in the default list may write to somebody's chat."""
    assert frozenset() == smoke.WRITE_CASES
    for case in smoke.CASES:
        assert case.name not in smoke.WRITE_CASES


def test_a_write_case_is_refused_without_the_flag(smoke, monkeypatch):
    monkeypatch.setattr(smoke, "WRITE_CASES", frozenset({"watch"}))
    assert smoke.main(["live_smoke"]) == 2


def test_a_missing_config_is_reported_not_raised(smoke, monkeypatch, tmp_path):
    monkeypatch.setattr(smoke, "config_path", lambda: tmp_path / "gone.yaml")
    assert smoke.main(["live_smoke"]) == 2


def test_receipts_directory_comes_from_the_config(smoke, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("logs: out/logs\n", encoding="utf-8")
    assert smoke.receipts_dir(config) == (tmp_path / "out/logs/receipts")


def test_newest_receipt_wins(smoke, tmp_path):
    old = tmp_path / "watch_chat-1.json"
    new = tmp_path / "watch_chat-2.json"
    old.write_text(json.dumps({"task_id": 1}), encoding="utf-8")
    new.write_text(json.dumps({"task_id": 2}), encoding="utf-8")
    import os
    os.utime(old, (1, 1))
    os.utime(new, (2, 2))
    assert smoke.newest_receipt(tmp_path, "watch_chat")["task_id"] == 2
    assert smoke.newest_receipt(tmp_path, "auto_reply") is None


def test_extra_cases_come_from_a_file(smoke, tmp_path):
    path = tmp_path / "live_cases.yaml"
    path.write_text(
        "cases:\n"
        "  - name: groups\n"
        "    args: [filter_group, --live, --accounts, '{account}',\n"
        "           --groups, '@demo']\n"
        "    expect: 'PLAN:'\n"
        "    receipt_for: filter_group\n",
        encoding="utf-8")
    cases, problems = smoke.load_extra(path)
    assert problems == []
    assert [case.name for case in cases] == ["groups"]
    assert cases[0].receipt_for == "filter_group"
    assert "{account}" in cases[0].args


def test_a_malformed_extra_case_is_a_problem(smoke, tmp_path):
    path = tmp_path / "live_cases.yaml"
    path.write_text("cases:\n  - name: noargs\n  - args: [x]\n",
                    encoding="utf-8")
    cases, problems = smoke.load_extra(path)
    assert cases == []
    assert len(problems) == 2


def test_a_missing_extra_file_is_normal(smoke, tmp_path):
    assert smoke.load_extra(tmp_path / "none.yaml") == ([], [])


def test_extra_cases_cannot_smuggle_a_write(smoke, tmp_path, monkeypatch):
    """An extra case is still a case: the write gate applies to it too."""
    monkeypatch.setattr(smoke, "WRITE_CASES", frozenset({"groups"}))
    monkeypatch.setattr(smoke, "config_path", lambda: tmp_path / "config.yaml")
    (tmp_path / "config.yaml").write_text("logs: l\n", encoding="utf-8")
    path = tmp_path / "live_cases.yaml"
    path.write_text("cases:\n  - name: groups\n    args: [filter_group]\n",
                    encoding="utf-8")
    monkeypatch.setattr(smoke, "extra_path", lambda: path)
    # The refusal happens before any config is read or any case is run.
    assert smoke.main(["live_smoke"]) == 2
