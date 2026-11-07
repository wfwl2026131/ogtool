"""The send admission queue, line by line.

The machine's three lines (:1738931, :1738933, :1738947) and the
scheduler's exception pair (:1738929-1738930).
"""

from __future__ import annotations

from ogtool.admission import (
    AdmissionQueue,
    exception_line,
)


def test_a_free_slot_is_granted_immediately():
    queue = AdmissionQueue()
    granted, waited = queue.acquire("t1", "acc1", timeout=1.0)
    assert granted is True and waited == 0


def test_a_busy_account_bypasses_with_the_wait_it_spent():
    import threading

    queue = AdmissionQueue()
    holder = queue._enqueue("t1", "acc1")  # holds the slot
    result: list = {}

    def try_send():
        result["r"] = queue.acquire("t2", "acc1", timeout=0.05, target="@g1")

    thread = threading.Thread(target=try_send)
    thread.start()
    thread.join(timeout=2.0)
    granted, waited = result["r"]
    assert granted is False
    assert 40 <= waited <= 2000  # the patience it spent, in ms
    queue.release("acc1", holder)


def test_waiters_are_granted_in_arrival_order():
    queue = AdmissionQueue()
    first = queue._enqueue("t1", "acc1")
    second = queue._enqueue("t2", "acc1")
    third = queue._enqueue("t3", "acc1")
    assert queue._queue_length("acc1") == 3
    queue.release("acc1", first)
    assert second.event.is_set()
    assert not third.event.is_set()
    queue.release("acc1", second)
    assert third.event.is_set()
    queue.release("acc1", third)
    assert queue._queue_length("acc1") == 0


def test_an_empty_queue_release_is_a_noop():
    queue = AdmissionQueue()
    queue.release("ghost", queue._enqueue("t1", "ghost"))
    assert queue._queue_length("ghost") == 0


def test_the_scheduler_exception_lines():
    assert exception_line(ValueError("boom")) == \
        "DeadlineScheduler task exception: boom"
    assert exception_line(None) == "DeadlineScheduler task exception: unknown"


def test_an_echoing_queue_prints_the_machine_lines(capsys):
    import threading

    queue = AdmissionQueue(echo=True)
    holder = queue._enqueue("t1", "acc1")
    result: list = {}

    def try_send():
        result["r"] = queue.acquire("t2", "acc1", timeout=0.05, target="@g1")

    thread = threading.Thread(target=try_send)
    thread.start()
    thread.join(timeout=2.0)
    queue.release("acc1", holder)
    out = capsys.readouterr().out
    assert "send admission enqueue task=t1 account=acc1" in out
    assert "send admission bypass task=t2 account=acc1 target=@g1 waited=" in out
