"""The wizard's menu covers every CLI feature, and its write gate holds."""

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


@pytest.fixture()
def wizard():
    module = _wizard()
    from ogtool.features import WRITE_FEATURES
    module.WRITE = set(WRITE_FEATURES)
    return module


def test_the_menu_covers_every_cli_command(wizard):
    """Every subcommand the CLI builds must be reachable from the menu."""
    from ogtool.cli import build_parser

    parser = build_parser()
    choices = set(parser._subparsers._group_actions[0].choices)
    # accounts 及其管理子页、runs/todo/selftest/evidence/bot_links/pattern
    # 是运维命令,不走向导(它们的交互价值低),菜单只承诺 25 个功能页。
    covered = (set(wizard.menu_order()) | {"accounts", "runs", "todo",
                                           "selftest", "evidence",
                                           "bot_links", "pattern"})
    assert choices - covered == set(), \
        f"CLI has pages the wizard never offers: {choices - covered}"
    assert len(wizard.menu_order()) == len(set(wizard.menu_order()))


def test_grouping_matches_the_registry(wizard):
    """The ⚠ group is exactly WRITE_FEATURES; the read group never writes."""
    order = wizard.menu_order()
    from ogtool.features import WRITE_FEATURES
    custom = wizard.MENU_CUSTOM_KEYS
    # generic ⚠ 组 = 其余写功能;定制写流程在 run_live 里自带同一道闸门
    assert {k for k in order if k in wizard.WRITE and k not in custom} \
        == set(WRITE_FEATURES) - custom
    read_keys = [k for k in order if k not in wizard.WRITE]
    for key in read_keys:
        assert key not in WRITE_FEATURES, f"{key} writes but sits in 只读组"


def test_every_flow_is_reachable(wizard):
    assert set(wizard.MENU_CUSTOM_KEYS) == set(wizard.FLOWS)
    for key in wizard.menu_order():
        assert key in wizard.FLOWS or key in wizard.GENERIC


def test_generic_flow_builds_argv(wizard, monkeypatch):
    """The generic engine turns answers into the flags the CLI accepts."""
    from ogtool.cli import build_parser

    parser = build_parser()
    parser_choices = parser._subparsers._group_actions[0].choices
    calls: list[list[str]] = []

    class FakeDone:
        returncode = 0

    answers = iter(["t1", "https://t.me/x", "hello", "30", "90", "no"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
    monkeypatch.setattr(subprocess, "run",
                        lambda argv, **kw: calls.append(list(argv))
                        or FakeDone())
    wizard.flow_generic("ai_chat")
    # calls[0] is the accounts-list peek; the feature run follows it
    feature_calls = [c for c in calls if "ai_chat" in c]
    assert feature_calls, f"ai_chat never invoked: {calls}"
    argv = feature_calls[0]
    assert "ai_chat" in argv
    # every flag the wizard passed must exist on that CLI page
    passed = {argv[i] for i in range(len(argv)) if argv[i].startswith("--")}
    page = parser_choices["ai_chat"]
    known = {a for a in page._actions for a in
             ([a.option_strings[0]] if a.option_strings else [])}
    assert passed <= known, f"wizard passed unknown flags: {passed - known}"


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


def test_run_live_requires_the_gate_word(wizard, captured, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _p="": "n")  # not yes
    wizard.run_live(["mass_send", "--text", "hi"], write=True)
    assert captured == [], "a write flow ran without the gate word"


def test_run_live_appends_allow_write_only_after_yes(wizard, captured,
                                                     monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _p="": "yes")
    wizard.run_live(["mass_send", "--text", "hi"], write=True)
    assert captured == [[sys.executable, "-m", "ogtool", "mass_send",
                         "--text", "hi", "--live", "--allow-write"]]


def test_read_only_flow_never_carries_allow_write(wizard, captured,
                                                  monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _p="": "")
    wizard.run_live(["accounts", "test", "--accounts", "t1"], write=False)
    assert captured == [[sys.executable, "-m", "ogtool", "accounts",
                         "test", "--accounts", "t1", "--live"]]


def test_the_wizard_finds_the_project_from_any_directory(wizard, monkeypatch):
    """A bare ogtool-wizard from $HOME still lands on a config.yaml."""
    import os
    import tempfile

    real_cwd = os.getcwd()
    repo = Path(real_cwd)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.chdir(tmp)
            # no config here, and the repo fallback is monkeypatched away
            monkeypatch.setattr(Path, "home",
                                lambda: Path(tmp) / "nowhere")
            wizard.locate_workdir()
            assert Path("config.yaml").is_file() is False  # nowhere to go
            # with the real home present it lands in the repo
            monkeypatch.setattr(Path, "home", lambda: repo.parent)
            (repo.parent / "tg-src" / "og-scripts").resolve()  # sanity
            if (repo.parent / "tg-src" / "og-scripts" / "config.yaml") \
                    .is_file():
                wizard.locate_workdir()
                assert Path("config.yaml").is_file()
    finally:
        monkeypatch.chdir(real_cwd)
