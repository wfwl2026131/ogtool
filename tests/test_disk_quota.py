"""#Q1 disk quota: the counters survive the process, the verdicts print.

The reference's QuotaDiskWriter (:2265509) keeps per-account daily counters
on disk with ``flushToDisk`` (:2241358) / ``isLimitReachedForNamespace``
(:2241357). ogtool's gap was the SlotMap budget and ``copy_chat``'s budget
dict, both pure memory; these tests pin the machine they now share.
"""

from __future__ import annotations

from datetime import date, timedelta

from ogtool.disk_quota import (
    COPY_CHAT,
    PUPPET_CHAT,
    DiskBudget,
    all_accounts_capped,
    record_send,
    seed_budget,
    selected_accounts_capped,
    within_limit,
)


def test_a_recorded_send_is_read_back(store):
    record_send(store, PUPPET_CHAT, "t1", ok=True)
    record_send(store, PUPPET_CHAT, "t1", ok=False)
    record_send(store, PUPPET_CHAT, "t2", ok=True)
    assert store.daily_quota_counts(PUPPET_CHAT, "t1", date.today().isoformat()) == (
        1,
        2,
    )
    assert store.daily_quota_counts(PUPPET_CHAT, "t2", date.today().isoformat()) == (
        1,
        1,
    )


def test_namespaces_do_not_share_counters(store):
    record_send(store, PUPPET_CHAT, "t1", ok=True)
    record_send(store, COPY_CHAT, "t1", ok=True)
    record_send(store, COPY_CHAT, "t1", ok=True)
    assert store.daily_quota_counts(PUPPET_CHAT, "t1", date.today().isoformat()) == (
        1,
        1,
    )
    assert store.daily_quota_counts(COPY_CHAT, "t1", date.today().isoformat()) == (2, 2)


def test_a_new_day_rolls_the_counters(store):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    store.bump_daily_quota(PUPPET_CHAT, "t1", yesterday, success=True)
    store.bump_daily_quota(PUPPET_CHAT, "t1", yesterday, success=True)
    assert store.daily_quota_counts(PUPPET_CHAT, "t1", yesterday) == (2, 2)
    assert store.daily_quota_counts(PUPPET_CHAT, "t1", date.today().isoformat()) == (
        0,
        0,
    )


def test_seed_budget_returns_today_only(store):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    store.bump_daily_quota(PUPPET_CHAT, "t1", yesterday, success=True)
    record_send(store, PUPPET_CHAT, "t1", ok=True)
    record_send(store, PUPPET_CHAT, "t2", ok=True)
    rows = seed_budget(store, PUPPET_CHAT, ["t1", "t2", "t3"])
    assert rows == {"t1": (1, 1), "t2": (1, 1), "t3": (0, 0)}


def test_disk_budget_take_is_capped_by_limit(store):
    budget = DiskBudget(store, PUPPET_CHAT, "t1", limit=2)
    assert budget.take() and budget.take()
    assert budget.take() is False
    assert budget.left == 0
    # The counters landed on disk: a fresh instance reads the same day.
    fresh = DiskBudget(store, PUPPET_CHAT, "t1", limit=2)
    assert fresh.used() == (2, 2)
    assert fresh.take() is False


def test_disk_budget_zero_limit_is_unlimited(store):
    budget = DiskBudget(store, PUPPET_CHAT, "t1", limit=0)
    for _ in range(3):
        assert budget.take()
    assert budget.left == 0  # unlimited reads as "no cap left to subtract"


def test_within_limit_checks_both_caps(store):
    today = date.today().isoformat()
    store.bump_daily_quota(PUPPET_CHAT, "t1", today, success=True)
    store.bump_daily_quota(PUPPET_CHAT, "t1", today, success=False)
    assert within_limit(store, PUPPET_CHAT, "t1", 2, 0)
    assert not within_limit(store, PUPPET_CHAT, "t1", 1, 0)
    assert not within_limit(store, PUPPET_CHAT, "t1", 0, 2)
    assert within_limit(store, PUPPET_CHAT, "t1", 0, 0)  # both unlimited


def test_prune_drops_old_days_but_keeps_two(store):
    old = (date.today() - timedelta(days=5)).isoformat()
    recent = (date.today() - timedelta(days=1)).isoformat()
    store.bump_daily_quota(PUPPET_CHAT, "t1", old, success=True)
    store.bump_daily_quota(PUPPET_CHAT, "t1", recent, success=True)
    store.bump_daily_quota(PUPPET_CHAT, "t1", date.today().isoformat(), success=True)
    removed = store.prune_daily_quota(date.today().isoformat(), keep_days=2)
    assert removed == 1
    assert store.daily_quota_counts(PUPPET_CHAT, "t1", old) == (0, 0)
    assert store.daily_quota_counts(PUPPET_CHAT, "t1", recent) == (1, 1)


def test_reset_needs_a_filter(store):
    try:
        store.reset_daily_quota()
    except ValueError as error:
        assert "at least one filter" in str(error)
    else:
        raise AssertionError("unfiltered reset was accepted")


def test_reset_clears_its_slice(store):
    record_send(store, PUPPET_CHAT, "t1", ok=True)
    record_send(store, COPY_CHAT, "t1", ok=True)
    removed = store.reset_daily_quota(namespace=PUPPET_CHAT, account="t1")
    assert removed == 1
    assert store.daily_quota_counts(PUPPET_CHAT, "t1", date.today().isoformat()) == (
        0,
        0,
    )
    assert store.daily_quota_counts(COPY_CHAT, "t1", date.today().isoformat()) == (1, 1)


def test_the_verdict_lines_print_verbatim():
    assert all_accounts_capped(3, 15, 21) == (
        "All 3 accounts reached per-account daily limits "
        "(success=15, attempt=21), wait until tomorrow"
    )
    assert selected_accounts_capped("10:00", 50, 0) == (
        "All selected accounts reached today's limits; will retry at 10:00. "
        "(Success limit: 50, Attempt limit: 0; 0 means unlimited.)"
    )


def test_the_slotmap_seeds_its_budget_from_the_store(store):
    from ogtool.features.puppet_chat import SlotMap

    record_send(store, PUPPET_CHAT, "t1", ok=True)
    record_send(store, PUPPET_CHAT, "t1", ok=True)
    slot_map = SlotMap(["t1", "t2"], daily_limit=2, store=store)
    # t1 is spent out, so the slot remaps to t2 instead of sending again.
    assert slot_map._usable("t1") is False
    assert slot_map._usable("t2") is True


def test_the_slotmap_records_live_results_to_disk(store):
    from ogtool.features.puppet_chat import SlotMap

    slot_map = SlotMap(["t1"], daily_limit=1, store=store)
    slot_map.note_result("t1", True)
    slot_map.note_result("t1", False, "PEER_FLOOD")
    assert store.daily_quota_counts(PUPPET_CHAT, "t1", date.today().isoformat()) == (
        1,
        2,
    )
    assert slot_map._usable("t1") is False


def test_a_dry_run_slotmap_without_store_spends_nothing(store):
    from ogtool.features.puppet_chat import SlotMap

    slot_map = SlotMap(["t1"], daily_limit=1)
    slot_map.note_result("t1", True)
    assert slot_map.budget == {"t1": 1}
    assert store.daily_quota_counts(PUPPET_CHAT, "t1", date.today().isoformat()) == (
        0,
        0,
    )


def test_copy_chat_seeds_and_writes_back(ctx, live, ok_sender):
    from ogtool.features.copy_chat import CopyConfig, run

    record_send(ctx.store, COPY_CHAT, "acc1", ok=True)
    record_send(ctx.store, COPY_CHAT, "acc1", ok=True)
    live(ok_sender)
    config = CopyConfig.from_dict(
        {
            "copy_chat": {
                "targets": "@sweep",
                "capture_seconds": 0,
                "messages": ["@src|@a|hello there"],
                "daily_limit": 3,
            }
        },
        None,
    )  # type: ignore[arg-type]
    assert run(config, ctx) == 0
    # The seed read 2 spent, the run's own sends landed on disk too.
    success, attempt = ctx.store.daily_quota_counts(
        COPY_CHAT, "acc1", date.today().isoformat()
    )
    assert success >= 3 and attempt >= 3


def test_quota_show_reads_the_table(ctx, capsys):
    from argparse import Namespace

    from ogtool.cli import _quota

    record_send(ctx.store, PUPPET_CHAT, "t1", ok=True)
    _quota(
        Namespace(day="", namespace="puppet_chat", account="", reset=False, yes=False),
        ctx.settings,
    )
    output = capsys.readouterr().out
    assert "puppet_chat" in output and "t1: success=1 attempt=1" in output


def test_quota_reset_requires_a_filter(capsys):
    from argparse import Namespace

    from ogtool.cli import _quota

    code = _quota(
        Namespace(day="", namespace="", account="", reset=True, yes=True), None
    )  # type: ignore[arg-type]
    assert code == 2
    assert "at least one filter" in capsys.readouterr().out
