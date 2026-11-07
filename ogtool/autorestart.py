"""The in-process auto-restart service (AutoRestartService, unbound).

The reference's own supervision loop (:1742794, the page documents it in
full): once enabled it checks for ``officegram-monitor.exe`` after 60
seconds, then every 45 seconds verifies the monitor/scheduled-restart
conditions, and force-kills itself 5 seconds after each trigger so logs
can flush.  A restart interval below one hour is only allowed when
``tdata/.debug`` exists - the settings page's own gate.

The state the page reports (:1742762-1742776) is one line at a time, in
a fixed priority order; ``AiChatExecutor`` requests the same machine
through three lines of its own (:1738305-1738309).  ``ThreadPoolOverloadGuard``
(:1742802-1742803) feeds the same trigger: the pool above its threshold
for 5 straight minutes, with the monitor running, self-kills - 0
disables it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The loop's own timing (:1742794).
STARTUP_DELAY_SEC = 60.0
POLL_INTERVAL_SEC = 45.0
FORCE_KILL_DELAY_SEC = 5.0
MIN_RESTART_SECONDS = 3600.0

#: The overload guard's window (:1742802-1742803): five straight minutes.
OVERLOAD_WINDOW_SEC = 300.0

MONITOR_EXE = "officegram-monitor.exe"

#: Monitor states, the page's own five (:1742765-1742769, :1742773).
MONITOR_RUNNING = "officegram-monitor.exe is running."
MONITOR_MISSING = "officegram-monitor.exe missing."
MONITOR_NOT_RUNNING = "officegram-monitor.exe not running."
MONITOR_START_REQUESTED = "officegram-monitor.exe start requested."
MONITOR_START_FAILED = "Failed to start officegram-monitor.exe."
MONITOR_UNAVAILABLE = "officegram-monitor.exe is unavailable."

#: The page's other state lines (:1742762-1742776).
LINE_NOT_ACTIVATED = "Not activated"
LINE_WAITING_STARTUP = "Waiting for startup delay."
LINE_SWITCH_OFF = "Scheduled restart switch is off."
LINE_FORMAT_INVALID = "Scheduled restart format is invalid."
LINE_BOTH_EMPTY = "Restart interval and scheduled restart are empty."
LINE_SCHEDULED_ACTIVE = "Scheduled restart is active."
LINE_TRIGGERED = "Restart triggered, waiting for force kill."
LINE_KILL_FAILED = "Force kill launch failed, timer re-armed."

#: AiChatExecutor's three lines (:1738305-1738309).
AICHAT_ALREADY_ARMED = (
    "AiChatExecutor fatal restart already armed, skip duplicate request: reason=%1"
)
AICHAT_REQUESTING = (
    "AiChatExecutor requesting delayed process restart: delay=%1s reason=%2"
)
AICHAT_RESULT = (
    "AiChatExecutor delayed restart result: armed=%1 scheduledNow=%2 "
    "monitorExists=%3 monitorRunning=%4 delaySeconds=%5 token=%6 reason=%7"
)
AICHAT_CANNOT_SCHEDULE = (
    "AI Chat start failed and automatic restart could not be scheduled."
)


def parse_scheduled_times(text: str) -> tuple[list[str], str]:
    """``hh:mm hh:mm`` -> two times, or the page's format refusal.

    The page's own placeholder is ``hh:mm hh:mm`` (:1742793): exactly two
    clock times, each a real ``HH:MM``.  Anything else is invalid, and an
    empty text is not invalid - it is simply unset, which the state
    machine reports separately.
    """
    raw = (text or "").strip()
    if not raw:
        return [], ""
    parts = [part for part in raw.replace(",", " ").split() if part]
    if len(parts) != 2:
        return [], LINE_FORMAT_INVALID
    for part in parts:
        if ":" not in part:
            return [], LINE_FORMAT_INVALID
        hours, _, minutes = part.partition(":")
        if not (hours.isdigit() and minutes.isdigit()):
            return [], LINE_FORMAT_INVALID
        if not (0 <= int(hours) <= 23 and 0 <= int(minutes) <= 59):
            return [], LINE_FORMAT_INVALID
    return parts, ""


def minutes_of(clock: str) -> int:
    """``HH:MM`` -> minutes since midnight."""
    hours, _, minutes = clock.partition(":")
    return int(hours) * 60 + int(minutes)


@dataclass(slots=True)
class AutoRestartRuntimeInfo:
    """One evaluation of the supervision loop's conditions."""

    enabled: bool = False
    ensure_monitor: bool = False
    interval_sec: float = 0.0
    scheduled_times: list[str] = field(default_factory=list)
    monitor_state: str = ""
    debug_open: bool = False
    started_at: float | None = None
    armed: bool = False
    token: str = ""


def state_line(info: AutoRestartRuntimeInfo) -> str:
    """The one line the page reports, in its own priority order.

    The config gate comes first (:1742770-1742773): the switch off, an
    invalid timetable, both sources empty, the monitor binary itself
    unavailable.  Then the activation and monitor lines, then the active
    verdict (:1742774).
    """
    if not info.enabled:
        return LINE_SWITCH_OFF
    if info.interval_sec <= 0 and not info.scheduled_times:
        return LINE_BOTH_EMPTY
    if (
        info.scheduled_times
        and not info.interval_sec
        and len(info.scheduled_times) != 2
    ):
        return LINE_FORMAT_INVALID
    if info.monitor_state == MONITOR_MISSING and not info.ensure_monitor:
        return MONITOR_MISSING
    if info.monitor_state == MONITOR_START_FAILED:
        return MONITOR_START_FAILED
    if info.monitor_state == MONITOR_UNAVAILABLE:
        return MONITOR_UNAVAILABLE
    if info.monitor_state == MONITOR_START_REQUESTED:
        return MONITOR_START_REQUESTED
    if info.armed:
        return LINE_TRIGGERED
    return LINE_SCHEDULED_ACTIVE


def restart_due(info: AutoRestartRuntimeInfo, now: float) -> tuple[bool, str]:
    """Whether this evaluation triggers a restart, and which line says so.

    The loop's own order (:1742794): the 60-second startup delay comes
    first, then the interval check, then the timetable - a time-of-day
    match on either of the two ``hh:mm`` entries.  The one-hour floor is
    the settings page's own gate: a shorter interval is only legal with
    ``tdata/.debug`` present, and a config that breaks it reports the
    monitor line rather than triggering.
    """
    if not info.enabled:
        return False, LINE_SWITCH_OFF
    if info.started_at is not None and now - info.started_at < STARTUP_DELAY_SEC:
        return False, LINE_WAITING_STARTUP
    if 0 < info.interval_sec < MIN_RESTART_SECONDS and not info.debug_open:
        return False, LINE_BOTH_EMPTY
    if info.interval_sec > 0:
        if info.started_at is not None and now - info.started_at >= info.interval_sec:
            return True, LINE_TRIGGERED
        return False, LINE_SCHEDULED_ACTIVE
    if info.scheduled_times:
        minute_of_day = int(now // 60) % 1440
        for clock in info.scheduled_times:
            if minutes_of(clock) == minute_of_day:
                return True, LINE_TRIGGERED
        return False, LINE_SCHEDULED_ACTIVE
    return False, LINE_BOTH_EMPTY


def request_delayed_restart(
    armed: bool,
    reason: str,
    delay_sec: float,
    monitor_exists: bool,
    monitor_running: bool,
    token: str,
) -> tuple[str, bool, str]:
    """AiChatExecutor's restart request, answered with its own three lines.

    A second request while one is already armed is refused with the
    duplicate line (:1738305); a first one is accepted and its result
    line names every fact the executor checks (:1738308).  The failure
    line (:1738309) is the caller's, when no monitor exists to arm with.
    """
    if armed:
        return AICHAT_ALREADY_ARMED.replace("%1", reason), False, token
    scheduled_now = monitor_exists
    result = (
        AICHAT_RESULT.replace("%1", "1" if scheduled_now else "0")
        .replace("%2", "1" if scheduled_now else "0")
        .replace("%3", "1" if monitor_exists else "0")
        .replace("%4", "1" if monitor_running else "0")
        .replace("%5", f"{delay_sec:g}")
        .replace("%6", token)
        .replace("%7", reason)
    )
    return (
        AICHAT_REQUESTING.replace("%1", f"{delay_sec:g}").replace("%2", reason),
        scheduled_now,
        result,
    )


def overload_triggered(
    history: list[tuple[float, float]], threshold: float, now: float
) -> bool:
    """ThreadPoolOverloadGuard's verdict (:1742802-1742803).

    The pool above ``threshold`` for the whole five-minute window and the
    threshold itself above zero - 0 disables the check, which is the
    page's own contract.  A gap inside the window breaks the streak.
    """
    if threshold <= 0:
        return False
    window_start = now - OVERLOAD_WINDOW_SEC
    in_window = [(stamp, value) for stamp, value in history if stamp >= window_start]
    if not in_window:
        return False
    if (now - in_window[0][0]) < OVERLOAD_WINDOW_SEC:
        return False
    return all(value > threshold for _stamp, value in in_window)


__all__ = [
    "AutoRestartRuntimeInfo",
    "FORCE_KILL_DELAY_SEC",
    "LINE_BOTH_EMPTY",
    "LINE_FORMAT_INVALID",
    "LINE_KILL_FAILED",
    "LINE_NOT_ACTIVATED",
    "LINE_SCHEDULED_ACTIVE",
    "LINE_SWITCH_OFF",
    "LINE_TRIGGERED",
    "LINE_WAITING_STARTUP",
    "MIN_RESTART_SECONDS",
    "MONITOR_EXE",
    "MONITOR_MISSING",
    "MONITOR_NOT_RUNNING",
    "MONITOR_RUNNING",
    "MONITOR_START_FAILED",
    "MONITOR_START_REQUESTED",
    "MONITOR_UNAVAILABLE",
    "OVERLOAD_WINDOW_SEC",
    "POLL_INTERVAL_SEC",
    "STARTUP_DELAY_SEC",
    "minutes_of",
    "overload_triggered",
    "parse_scheduled_times",
    "request_delayed_restart",
    "restart_due",
    "state_line",
]
