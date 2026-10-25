"""#24 settings: the six knobs, the rules, and the two lines about them.

The config loader already refuses an impossible number, so these cases build
the runtime object directly: the page is the second line of defence, and a
second line that never fires is not a line at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

import pytest

from ogtool.config import GlobalConfig
from ogtool.features import settings as page


@dataclass(slots=True)
class _Ctx:
    settings: object
    store: object
    live: bool = False
    task_id: object = None
    waited: float = 0.0

    def note(self, *_args, **_kwargs) -> None:
        """The page never warns, but every context has the method."""


def _ctx(runtime: GlobalConfig, tmp_path) -> _Ctx:
    from ogtool.store import Store

    holder = type("Holder", (), {"runtime": runtime, "path": "config.yaml"})()
    return _Ctx(settings=holder, store=Store(tmp_path / "og.sqlite"))


def test_the_defaults_break_no_rule():
    assert page.problems(GlobalConfig()) == []


@pytest.mark.parametrize(("field", "value"), (
    ("account_batch", 0),
    ("threads_max", 0),
    ("resume_delay", -1.0),
    ("tp_overload", -1),
    ("restart_interval", -1.0),
))
def test_a_knob_that_cannot_be_used_is_reported(field: str, value: object):
    runtime = replace(GlobalConfig(), **{field: value})
    found = page.problems(runtime)
    assert len(found) == 1
    assert found[0].startswith(field)
    assert "EX" not in found[0]


def test_the_restart_line_describes_and_never_asks():
    assert page.restart_due(GlobalConfig()) == "off"
    uptime = replace(GlobalConfig(), restart_interval=60.0)
    assert page.restart_due(uptime) == "60s uptime"
    timed = replace(GlobalConfig(), restart_interval=120.0,
                    restart_timed=True)
    assert page.restart_due(timed) == "120s timed"


def test_the_overload_line_says_off_at_zero():
    assert page.overload_line(GlobalConfig()) == "off"
    assert page.overload_line(replace(GlobalConfig(), tp_overload=7)) == "7"


def test_the_page_passes_and_returns_zero(capsys, tmp_path):
    assert page.run(page.SettingsPageConfig(), _ctx(GlobalConfig(), tmp_path)) == 0
    output = capsys.readouterr().out
    assert "account_batch" in output
    assert "restart due at: off" in output
    for key, label, _rule in page.KNOBS:
        assert key in output
        assert label in output


def test_the_page_fails_on_a_knob_it_reports(capsys, tmp_path):
    bad = replace(GlobalConfig(), threads_max=0)
    assert page.run(page.SettingsPageConfig(), _ctx(bad, tmp_path)) == 1
    assert "PROBLEM" in capsys.readouterr().out


def test_the_json_stays_one_key_per_knob(capsys, tmp_path):
    assert page.run(page.SettingsPageConfig(as_json=True),
                    _ctx(GlobalConfig(), tmp_path)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert list(payload) == [key for key, _label, _rule in page.KNOBS]
    assert len(payload) == 6


def test_the_json_reports_problems_on_stderr(capsys, tmp_path):
    bad = replace(GlobalConfig(), account_batch=0)
    code = page.run(page.SettingsPageConfig(as_json=True), _ctx(bad, tmp_path))
    captured = capsys.readouterr()
    assert code == 1
    assert "PROBLEM" in captured.err
    assert "PROBLEM" not in captured.out
    assert json.loads(captured.out)["account_batch"] == 0


def test_the_audit_leaves_a_row_of_its_own(capsys, tmp_path):
    """A clean audit is still work: one planned row says what was checked."""
    from ogtool.config import Settings
    from ogtool.runtime import RunContext
    from ogtool.store import Store

    settings = Settings(tmp_path / "config.yaml", log_dir=tmp_path / "logs")
    ctx = RunContext(settings, Store(tmp_path / "og.sqlite"),
                     __import__("logging").getLogger("t"))
    assert page.run(page.SettingsPageConfig(), ctx) == 0

    import json

    receipt = json.loads(
        (sorted((tmp_path / "logs" / "receipts").glob("settings-*.json"))[-1])
        .read_text())
    assert receipt["planned"] == 1
    assert receipt["ok"] == 1 and receipt["failed"] == 0
    assert receipt["results"]["planned"] == 1


def test_a_problem_is_one_failed_row(capsys, tmp_path):
    from ogtool.config import Settings
    from ogtool.runtime import RunContext
    from ogtool.store import Store

    settings = Settings(tmp_path / "config.yaml", log_dir=tmp_path / "logs")
    ctx = RunContext(settings, Store(tmp_path / "og.sqlite"),
                     __import__("logging").getLogger("t"))
    bad = replace(GlobalConfig(), threads_max=0)
    ctx.settings.runtime = bad
    assert page.run(page.SettingsPageConfig(), ctx) == 1

    import json

    receipt = json.loads(
        (sorted((tmp_path / "logs" / "receipts").glob("settings-*.json"))[-1])
        .read_text())
    assert receipt["failed"] == 1
    assert receipt["results"]["failed"] == 1
