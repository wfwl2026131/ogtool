"""The wizard's menu only offers real features, and its write gate holds."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _wizard():
    spec = importlib.util.spec_from_file_location(
        "ogtool_wizard", ROOT / "ogtool" / "wizard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_menu_entry_is_a_real_cli_feature():
    from ogtool.cli import build_parser

    wizard = _wizard()
    parser = build_parser()
    choices = set(parser._subparsers._group_actions[0].choices)
    for key, _label in wizard.MENU:
        if key == "test":
            assert "accounts" in choices
            continue
        assert key in choices, f"wizard offers {key}, the CLI does not"


def test_every_flow_has_a_menu_entry():
    wizard = _wizard()
    keys = {key for key, _label in wizard.MENU}
    assert set(wizard.FLOWS) == keys


@pytest.fixture()
def captured(monkeypatch):
    """Stub subprocess.run and the terminal, record every argv."""
    calls: list[list[str]] = []

    class FakeDone:
        returncode = 0

    monkeypatch.setattr(subprocess, "run",
                        lambda argv, **kw: calls.append(list(argv))
                        or FakeDone())
    return calls


def test_run_live_requires_the_gate_word(captured, monkeypatch):
    wizard = _wizard()
    monkeypatch.setattr("builtins.input",
                        lambda _p="": "n")  # anything but yes
    wizard.run_live(["mass_send", "--text", "hi"], write=True)
    assert captured == [], "a write flow ran without the gate word"


def test_run_live_appends_allow_write_only_after_yes(captured, monkeypatch):
    wizard = _wizard()
    monkeypatch.setattr("builtins.input", lambda _p="": "yes")
    wizard.run_live(["mass_send", "--text", "hi"], write=True)
    assert captured == [[sys.executable, "-m", "ogtool", "mass_send",
                         "--text", "hi", "--live", "--allow-write"]]


def test_read_only_flow_never_carries_allow_write(captured, monkeypatch):
    wizard = _wizard()
    monkeypatch.setattr("builtins.input", lambda _p="": "")
    wizard.run_live(["accounts", "test", "--accounts", "t1"], write=False)
    assert captured == [[sys.executable, "-m", "ogtool", "accounts",
                         "test", "--accounts", "t1", "--live"]]
