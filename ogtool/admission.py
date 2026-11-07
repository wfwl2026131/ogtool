"""The send admission queue (SendMessageAdmissionQueue / schedulePump).

The executor family's throttle (:2274274): every outgoing send is
enqueued per (task, account), granted its turn in order, and a send that
waited past the queue's patience goes through with the bypass line
naming what it waited.  The machine speaks three lines (:1738931,
:1738933, :1738947):

    send admission enqueue task=%1 account=%2
    send admission granted task=%1 account=%2
    send admission bypass task=%1 account=%2 target=%3 waited=%4ms

``DeadlineScheduler`` (:1738929-1738930) is the timer underneath: a task
that raises reports its exception, or ``unknown`` when the failure has
no name.
"""

from __future__ import annotations

import contextlib
import threading
from collections import deque
from dataclasses import dataclass, field

#: The queue's three lines, verbatim (:1738931, :1738933, :1738947).
LINE_ENQUEUE = "send admission enqueue task=%1 account=%2"
LINE_GRANTED = "send admission granted task=%1 account=%2"
LINE_BYPASS = "send admission bypass task=%1 account=%2 target=%3 waited=%4ms"

#: The scheduler's own failure lines (:1738929-1738930).
LINE_TASK_EXCEPTION = "DeadlineScheduler task exception: %1"
LINE_TASK_EXCEPTION_UNKNOWN = "DeadlineScheduler task exception: unknown"


def exception_line(error: BaseException | None) -> str:
    """The scheduler's exception line, or its ``unknown`` fallback."""
    name = str(error) if error is not None else ""
    return (
        LINE_TASK_EXCEPTION.replace("%1", name) if name else LINE_TASK_EXCEPTION_UNKNOWN
    )


@dataclass(slots=True)
class _Waiter:
    """One enqueued send, in the order the queue received it."""

    task: str
    account: str
    event: threading.Event = field(default_factory=threading.Event)


class AdmissionQueue:
    """One send at a time per account, tasks queueing in arrival order.

    The pump (:2274274 ``schedulePump``) grants the head of the account's
    queue; when that send finishes the next one is granted.  A caller
    that cannot wait hands over with ``bypass`` instead: the line names
    the target and how long the queue would have made it wait.
    """

    def __init__(self, echo: bool = False) -> None:
        self.echo = echo
        self._locks: dict[str, list[_Waiter]] = {}
        self._guard = threading.Lock()

    def _say(self, line: str) -> None:
        if self.echo:
            print(line)

    def _enqueue(self, task: str, account: str) -> _Waiter:
        waiter = _Waiter(task=task, account=account)
        with self._guard:
            self._locks.setdefault(account, deque()).append(waiter)
        self._say(LINE_ENQUEUE.replace("%1", task).replace("%2", account))
        return waiter

    def _queue_length(self, account: str) -> int:
        with self._guard:
            return len(self._locks.get(account, ()))

    def acquire(
        self, task: str, account: str, timeout: float | None = None, target: str = "-"
    ) -> tuple[bool, int]:
        """Take the account's send slot. ``(granted, waited_ms)``.

        The queue is waited on for ``timeout`` seconds at most: a caller
        whose patience runs out bypasses (:1738947), and the line's
        ``waited=%4ms`` is the wait it actually spent before giving up.
        A free slot is granted immediately with zero wait.
        """
        import time as _time

        started = _time.monotonic()
        if not self._queue_length(account):
            # The slot is free: granted on the spot, nothing queued.
            self._say(LINE_GRANTED.replace("%1", task).replace("%2", account))
            return True, 0
        waiter = self._enqueue(task, account)
        if waiter.event.wait(timeout if timeout is not None else None):
            waited_ms = int((_time.monotonic() - started) * 1000)
            self.release(account, waiter)
            self._say(LINE_GRANTED.replace("%1", task).replace("%2", account))
            return True, waited_ms
        waited_ms = int(timeout * 1000) if timeout is not None else 0
        self.release(account, waiter)
        self._say(
            LINE_BYPASS.replace("%1", task)
            .replace("%2", account)
            .replace("%3", target)
            .replace("%4", str(waited_ms))
        )
        return False, waited_ms

    def release(self, account: str, waiter: _Waiter) -> None:
        """Grant the next waiter, or retire the queue when it is empty."""
        with self._guard:
            queue = self._locks.get(account)
            if not queue:
                return
            with contextlib.suppress(ValueError):
                queue.remove(waiter)
            if queue and queue[0] is not waiter:
                queue[0].event.set()


def task_exception_line(error: BaseException | None) -> str:
    """Alias kept for the scheduler half of the family."""
    return exception_line(error)


__all__ = [
    "AdmissionQueue",
    "LINE_BYPASS",
    "LINE_ENQUEUE",
    "LINE_GRANTED",
    "LINE_TASK_EXCEPTION",
    "LINE_TASK_EXCEPTION_UNKNOWN",
    "exception_line",
    "task_exception_line",
]
