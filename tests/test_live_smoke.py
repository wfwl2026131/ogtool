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


def test_a_case_file_cannot_declare_a_write_it_will_run(smoke, tmp_path,
                                                        monkeypatch):
    """'write: true' in the yaml has to be honoured, not just documented.

    It was not: the gate only looked at the hard-coded WRITE_CASES, so a case file
    that said write: true ran anyway. Found by adding one and watching it run.
    """
    monkeypatch.setattr(smoke, "WRITE_CASES", frozenset())
    monkeypatch.setattr(smoke, "config_path", lambda: tmp_path / "config.yaml")
    (tmp_path / "config.yaml").write_text("logs: l\n", encoding="utf-8")
    path = tmp_path / "live_cases.yaml"
    path.write_text("cases:\n  - name: shout\n    args: [mass_send]\n"
                    "    write: true\n", encoding="utf-8")
    monkeypatch.setattr(smoke, "extra_path", lambda: path)
    assert smoke.main(["live_smoke"]) == 2


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


def test_the_proxy_case_opens_its_own_listener(smoke):
    """A proxy probe needs a target; a stranger's host is not one."""
    module = smoke
    case = next(item for item in module.CASES if item.name == "proxy")
    assert case.local_port is True
    assert "{port}" in " ".join(case.args)
    assert case.receipt_for == "proxy"


def test_a_local_listener_is_open_and_has_a_port(smoke):
    module = smoke
    server, port = module.local_listener()
    try:
        assert port > 0
        assert server.fileno() >= 0
    finally:
        server.close()


def test_every_write_page_in_the_cases_carries_the_flag(smoke):
    """A case naming a write page must be marked, or the CLI stops the suite.

    That is what happened when the gate went in: the ``listen`` case ran
    auto_reply and the whole run died on the refusal. The mark is not a bypass -
    it is the claim that this particular case cannot write, or writes only into
    the account's own chat.
    """
    from ogtool.features import WRITE_FEATURES, load_defaults

    load_defaults()
    for case in smoke.CASES:
        page = case.args[0]
        if page not in WRITE_FEATURES:
            continue
        assert case.cli_write or case.write, (
            f"{case.name} runs the write page {page} without being marked")
        assert case.args.count("--allow-write") == 0, (
            f"{case.name} hardcodes --allow-write; run_case inserts it")


def test_the_positive_half_of_the_gate_writes_to_self(smoke):
    """The one case that proves --allow-write on a real machine writes no third
    party: its target is ``{self}``."""
    case = next(c for c in smoke.CASES if c.name == "send")
    assert case.cli_write
    assert "{self}" in case.args
    assert case.receipt_for == "mass_send"


def test_a_live_case_needs_a_live_task_with_rows(smoke):
    """Exit 0 with a task that recorded nothing proves less than it looks."""
    case = smoke.Case("x", ("keep_online",), None, "keep_online", "",
                      min_rows=1)
    dry = {"mode": "dry-run", "task_id": 9, "results": {"total": 1}}
    empty = {"mode": "live", "task_id": 9, "results": {"total": 0}}
    good = {"mode": "live", "task_id": 9, "results": {"total": 2}}
    assert smoke.shallow_receipt(case, dry) == [
        "x: receipt is dry-run task=9, expected a live task"]
    assert smoke.shallow_receipt(case, empty) == [
        "x: receipt holds 0 row(s), expected at least 1"]
    assert smoke.shallow_receipt(case, good) == []


def test_a_listening_case_does_not_demand_rows(smoke):
    """watch_chat and auto_reply may legitimately see nothing in eight seconds."""
    case = smoke.Case("listen", ("auto_reply",), None, "auto_reply", "")
    assert smoke.shallow_receipt(case, {"mode": "live", "task_id": 1,
                                        "results": {"total": 0}}) == []


def test_a_case_can_stay_out_of_the_suite_but_still_be_runnable(smoke):
    """copy_chat repeats its whole copy on every run: real proof, bad habit.

    The case is registered and runs by name (--only copy); the six checks do
    not pay for it.
    """
    names = {case.name: case for case in smoke.CASES}
    assert names["copy"].in_suite is False
    assert names["copy"].cli_write is True
    assert names["copy"].min_rows == 1
    # Everything else still runs in the suite.
    assert sorted(name for name, case in names.items() if not case.in_suite)         == ["clone", "copy", "invite", "invite2", "join", "login", "rename",
        "script", "stage1", "video"]


def test_a_negative_path_may_exit_nonzero(smoke, tmp_path):
    """The write gate exits 2 on purpose; a case that expects that refusal
    (the negative-path pattern, like video_manage's no-call probe) declares
    the expected exit instead of failing its own check."""
    config = tmp_path / "c.yaml"
    config.write_text("accounts:\n  t1: {}\n")
    refused = ("mass_send", "--live", "--accounts", "t1")
    case = smoke.Case("x", refused, None, "", "", allow_exit=2)
    assert smoke.Case("y", refused, None, "", "").allow_exit == 0
    assert smoke.run_case(case, config, "t1", "") == []
    problems = smoke.run_case(
        smoke.Case("y", refused, None, "", ""), config, "t1", "")
    assert any("exit 2" in line for line in problems)
