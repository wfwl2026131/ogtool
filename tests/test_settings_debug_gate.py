"""The hidden gate: a restart interval under an hour needs ``tdata/.debug``.

Nothing in the interface mentions the marker except the refusal text itself,
which the dump carries four times (tooltip, two dialog messages, one log line):
"Restart interval cannot be less than 1 hour unless tdata/.debug exists."
That sentence is the specification, so it is the message here too.
"""

from __future__ import annotations

from ogtool.features.settings import (
    DEBUG_MARKER,
    MIN_RESTART_SECONDS,
    SettingsPageConfig,
    debug_gate_open,
    problems,
    run,
)


class _Runtime:
    def __init__(self, interval: float):
        self.account_batch = 20
        self.threads_max = 128
        self.resume_delay = 0.0
        self.tp_overload = 0
        self.restart_interval = interval
        self.restart_timed = False


def test_the_floor_is_one_hour():
    assert MIN_RESTART_SECONDS == 3600.0
    assert DEBUG_MARKER == "tdata/.debug"


def test_an_interval_under_an_hour_is_refused_without_the_marker(tmp_path):
    found = problems(_Runtime(1800), tmp_path)
    assert len(found) == 1
    assert "tdata/.debug" in found[0]
    assert "1800" in found[0]


def test_the_marker_opens_the_gate(tmp_path):
    marker = tmp_path / "tdata" / ".debug"
    marker.parent.mkdir(parents=True)
    marker.touch()
    assert debug_gate_open(tmp_path) is True
    assert problems(_Runtime(1800), tmp_path) == []


def test_a_marker_in_the_wrong_place_does_not_count(tmp_path):
    (tmp_path / ".debug").touch()
    assert debug_gate_open(tmp_path) is False
    assert problems(_Runtime(60), tmp_path)


def test_an_hour_or_more_needs_no_marker(tmp_path):
    assert problems(_Runtime(3600), tmp_path) == []
    assert problems(_Runtime(7200), tmp_path) == []


def test_zero_still_means_off(tmp_path):
    assert problems(_Runtime(0), tmp_path) == []


def test_the_page_names_the_gate(ctx, tmp_path, capsys):
    run(SettingsPageConfig(), ctx)
    output = capsys.readouterr().out
    assert "debug gate (tdata/.debug):" in output


def test_a_refused_interval_is_a_problem_and_the_run_reports_it(ctx, capsys):
    runtime = ctx.settings.runtime
    runtime.restart_interval = 900
    assert run(SettingsPageConfig(), ctx) == 1
    output = capsys.readouterr().out
    assert "PROBLEM: restart_interval" in output
