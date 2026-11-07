"""The restore-mode machine, verdict by verdict.

The reference's three sentences (:1744030, :1742974-1742975), the per-
page flag lines (:1741844, :1744844, :1744967, :1745088, :1745148) and
the settings page's delay knob (:1742796-1742797).
"""

from __future__ import annotations

from ogtool import restore
from ogtool.restore import RestorePlan, plan_all, plan_restore, restore_line


def test_the_mode_line_names_the_flag():
    assert restore_line("auto login") == "Restore mode: auto login"


def test_the_newest_receipt_of_a_feature_is_the_candidate(settings, store):
    import logging

    from ogtool.features._common import close_task, open_task, record
    from ogtool.runtime import RunContext

    context = RunContext(
        settings=settings, store=store, log=logging.getLogger("t"), live=False
    )
    task = open_task(context, "first", "copy_chat", {"targets": ["@a"]}, 2)
    record(context, task, "acc1", "@a", True)
    close_task(context, task, ok=1, failed=0)
    task = open_task(context, "second", "copy_chat", {"targets": ["@b"]}, 3)
    close_task(context, task, ok=0, failed=0, status="stopped")

    plan = plan_restore(settings, "copy_chat")
    assert plan.ok and plan.task_id == str(task.id)
    assert "Successfully restored task" in plan.line
    assert plan.config["targets"] == ["@b"]
    context.close()


def test_a_feature_without_receipts_starts_from_the_beginning(settings):
    plan = plan_restore(settings, "puppet_chat")
    assert not plan.ok
    assert (
        plan.line == "Failed to restore task puppet_chat state, starting from beginning"
    )


def test_a_receipt_without_resumable_state_fails_the_restore(settings, store):
    import logging

    from ogtool.features._common import close_task, open_task
    from ogtool.runtime import RunContext

    context = RunContext(
        settings=settings, store=store, log=logging.getLogger("t"), live=False
    )
    task = open_task(context, "thin", "copy_chat", {"x": 1}, 0)
    close_task(context, task, ok=0, failed=0)
    plan = plan_restore(settings, "copy_chat")
    assert not plan.ok and plan.task_id == str(task.id)
    assert "starting from beginning" in plan.line
    context.close()


def test_the_delay_rides_the_success_line(settings, store):
    import logging

    from ogtool.features._common import close_task, open_task
    from ogtool.runtime import RunContext

    context = RunContext(
        settings=settings, store=store, log=logging.getLogger("t"), live=False
    )
    task = open_task(context, "delayed", "copy_chat", {"seed": 1}, 1)
    close_task(context, task, ok=0, failed=0)
    plan = plan_restore(settings, "copy_chat", delay_sec=2.5)
    assert "(delay 2.5s)" in plan.line
    context.close()


def test_the_sweep_covers_every_page_with_a_flag_line(settings):
    plans = plan_all(settings)
    assert [plan.feature for plan in plans] == list(restore.RESTORABLE)
    assert all(isinstance(plan, RestorePlan) for plan in plans)
    assert all("restore" in plan.line or "restored" in plan.line for plan in plans)


def test_the_line_constant_matches_the_dump():
    assert restore.LINE_MODE == "Restore mode: %1"
    assert restore.LINE_OK == "Successfully restored task %1 execution state"
    assert restore.LINE_FAILED == (
        "Failed to restore task %1 state, starting from beginning"
    )
