"""The in-process auto-restart machine, line by line.

The page's own state lines (:1742762-1742776), the loop timing
(:1742794), the one-hour debug gate, AiChat's three request lines
(:1738305-1738309) and the ThreadPoolOverloadGuard's five-minute streak
(:1742802-1742803).
"""

from __future__ import annotations

from ogtool.autorestart import (
    LINE_BOTH_EMPTY,
    LINE_FORMAT_INVALID,
    LINE_SCHEDULED_ACTIVE,
    LINE_SWITCH_OFF,
    LINE_TRIGGERED,
    LINE_WAITING_STARTUP,
    MONITOR_MISSING,
    MONITOR_START_FAILED,
    OVERLOAD_WINDOW_SEC,
    AutoRestartRuntimeInfo,
    minutes_of,
    overload_triggered,
    parse_scheduled_times,
    request_delayed_restart,
    restart_due,
    state_line,
)


def _info(**overrides) -> AutoRestartRuntimeInfo:
    values: dict = {
        "enabled": True,
        "interval_sec": 7200.0,
        "started_at": 0.0,
    }
    values.update(overrides)
    return AutoRestartRuntimeInfo(**values)


def test_the_timetable_parses_two_clock_times():
    times, error = parse_scheduled_times("08:30 22:00")
    assert times == ["08:30", "22:00"] and error == ""
    assert parse_scheduled_times("")[1] == ""
    assert parse_scheduled_times("8:30")[1] == LINE_FORMAT_INVALID
    assert parse_scheduled_times("25:00 08:00")[1] == LINE_FORMAT_INVALID
    assert parse_scheduled_times("aa:bb 08:00")[1] == LINE_FORMAT_INVALID


def test_minutes_of_a_clock():
    assert minutes_of("00:00") == 0
    assert minutes_of("23:59") == 23 * 60 + 59


def test_the_state_line_follows_the_page_priority():
    assert state_line(_info(enabled=False)) == LINE_SWITCH_OFF
    assert state_line(_info(interval_sec=0)) == LINE_BOTH_EMPTY
    assert state_line(_info(monitor_state=MONITOR_MISSING)) == MONITOR_MISSING
    assert state_line(_info(monitor_state=MONITOR_START_FAILED)) == MONITOR_START_FAILED
    assert (
        state_line(_info(scheduled_times=["08:30", "22:00"])) == LINE_SCHEDULED_ACTIVE
    )
    assert state_line(_info()) == LINE_SCHEDULED_ACTIVE
    assert (
        state_line(_info(interval_sec=0, scheduled_times=["bad"]))
        == LINE_FORMAT_INVALID
    )
    info = _info(interval_sec=0)
    info.scheduled_times = []
    assert state_line(info) == LINE_BOTH_EMPTY


def test_the_loop_waits_its_startup_delay_then_triggers_on_the_interval():
    due, line = restart_due(_info(started_at=100.0), now=130.0)
    assert not due and line == LINE_WAITING_STARTUP
    due, line = restart_due(
        _info(started_at=100.0, interval_sec=100.0, debug_open=True), now=200.0
    )
    assert due and line == LINE_TRIGGERED
    due, line = restart_due(_info(started_at=100.0, interval_sec=7200.0), now=200.0)
    assert not due and line == LINE_SCHEDULED_ACTIVE


def test_the_one_hour_gate_needs_the_debug_marker():
    due, line = restart_due(
        _info(started_at=0.0, interval_sec=1800.0, debug_open=False), now=1900.0
    )
    assert not due and line == LINE_BOTH_EMPTY
    due, line = restart_due(
        _info(started_at=0.0, interval_sec=1800.0, debug_open=True), now=1900.0
    )
    assert due and line == LINE_TRIGGERED


def test_a_timetable_triggers_on_the_minute_of_day():
    eight_thirty = 8 * 3600 + 30 * 60
    due, _ = restart_due(
        _info(interval_sec=0.0, scheduled_times=["08:30", "22:00"]),
        now=eight_thirty,
    )
    assert due
    due, line = restart_due(
        _info(interval_sec=0.0, scheduled_times=["08:30", "22:00"]),
        now=eight_thirty + 60,
    )
    assert not due and line == LINE_SCHEDULED_ACTIVE


def test_aichat_request_lines_carry_every_fact():
    asking, armed, result = request_delayed_restart(
        armed=False,
        reason="start failed",
        delay_sec=30.0,
        monitor_exists=True,
        monitor_running=True,
        token="t9",
    )
    assert armed
    assert "delay=30s reason=start failed" in asking
    assert "armed=1 scheduledNow=1 monitorExists=1 monitorRunning=1" in result
    assert "delaySeconds=30 token=t9 reason=start failed" in result
    duplicate, armed2, _ = request_delayed_restart(
        armed=True,
        reason="again",
        delay_sec=30.0,
        monitor_exists=True,
        monitor_running=True,
        token="t9",
    )
    assert not armed2
    assert "already armed" in duplicate and "reason=again" in duplicate


def test_the_overload_guard_needs_a_full_streak_above_the_threshold():
    stamps = [0.0, 60.0, 120.0, 180.0, 240.0]
    history = [(stamp, 200.0) for stamp in stamps]
    assert overload_triggered(history, threshold=128, now=OVERLOAD_WINDOW_SEC)
    assert not overload_triggered(history, threshold=128, now=240.0)
    assert not overload_triggered(history, threshold=0, now=OVERLOAD_WINDOW_SEC)
    dipped = history[:3] + [(180.0, 10.0), (240.0, 200.0)]
    assert not overload_triggered(dipped, threshold=128, now=OVERLOAD_WINDOW_SEC)
    assert not overload_triggered([], threshold=128, now=OVERLOAD_WINDOW_SEC)
