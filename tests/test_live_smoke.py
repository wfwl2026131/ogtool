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
