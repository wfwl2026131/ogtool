"""SQLite store: accounts, tasks, results and the daily counter."""

from __future__ import annotations

from ogtool.store import Account, Store


def make_store(tmp_path):
    return Store(tmp_path / "og.sqlite")


def test_accounts_round_trip(tmp_path):
    with make_store(tmp_path) as store:
        store.add_account(Account("8613800000000", phone="+8613800000000"))
        store.add_account(Account("8613900000000", phone="+8613900000000"))
        rows = store.list_accounts()
        assert [row.label for row in rows] == ["8613800000000", "8613900000000"]

        store.add_account(Account("8613800000000", phone="+8613800000001"))
        again = store.get_account("8613800000000")
        assert again is not None and again.phone == "+8613800000001"
        assert len(store.list_accounts()) == 2


def test_account_status(tmp_path):
    with make_store(tmp_path) as store:
        store.add_account(Account("acc"))
        store.set_account_status("acc", "ok")
        row = store.get_account("acc")
        assert row is not None and row.status == "ok"


def test_task_lifecycle(tmp_path):
    with make_store(tmp_path) as store:
        task = store.create_task("demo", "mass_send", {"a": 1}, planned=3)
        store.finish_task(task.id, sent=2, failed=1)
        stats = store.stats()
        assert stats["tasks"] == 1
        assert stats["accounts"] == 0


def test_results_and_daily_counter(tmp_path):
    with make_store(tmp_path) as store:
        task = store.create_task("demo", "mass_send")
        store.add_result(task.id, "acc", "@a", "sent")
        store.add_result(task.id, "acc", "@b", "sent")
        store.add_result(task.id, "acc", "@c", "failed", "PEER_FLOOD")
        assert store.count_sent_today("acc") == 2
        assert len(store.results_for(task.id)) == 3
        assert store.count_sent_today("other") == 0


def test_target_list_replaces_rows(tmp_path):
    with make_store(tmp_path) as store:
        first = store.save_target_list("demo", "custom", [("group", "@a", "")])
        second = store.save_target_list("demo", "custom",
                                       [("group", "@b", ""), ("group", "@c", "")])
        assert first == second
        rows = store._conn.execute("SELECT ref FROM targets ORDER BY ref").fetchall()
        assert [row["ref"] for row in rows] == ["@b", "@c"]


def test_logs_are_written(tmp_path):
    with make_store(tmp_path) as store:
        task = store.create_task("demo", "keep_online")
        store.add_log(task.id, "hello", "info")
        assert store.stats()["logs"] == 1


def test_the_daily_day_bounds_convert_local_midnight_to_utc():
    from datetime import datetime, timedelta, timezone

    from ogtool.store import local_day_bounds

    east = timezone(timedelta(hours=8))
    start, end = local_day_bounds(datetime(2026, 10, 24, 0, 30, tzinfo=east))
    assert start == "2026-10-23T16:00:00+00:00"
    assert end == "2026-10-24T16:00:00+00:00"


def test_the_daily_counter_follows_the_local_day_across_midnight(tmp_path):
    from datetime import UTC, datetime, timedelta

    with make_store(tmp_path) as store:
        task = store.create_task("demo", "mass_send")
        store.add_result(task.id, "acc", "@a", "sent")
        local_midnight = datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0)
        now = local_midnight + timedelta(minutes=5)

        after = (local_midnight + timedelta(seconds=1)).astimezone(UTC).replace(
            microsecond=0).isoformat()
        store._conn.execute("UPDATE results SET created_at = ?", (after,))
        store._conn.commit()
        assert store.count_sent_today("acc", now=now) == 1

        before = (local_midnight - timedelta(seconds=1)).astimezone(UTC).replace(
            microsecond=0).isoformat()
        store._conn.execute("UPDATE results SET created_at = ?", (before,))
        store._conn.commit()
        assert store.count_sent_today("acc", now=now) == 0


def test_two_processes_can_write_at_once(tmp_path):
    """The store is shared between concurrent tools; WAL lets them coexist.

    Before this, 'watch_chat' holding the file while 'mass_send' wrote produced
    'OperationalError: database is locked' -- a reader and a writer cannot both
    hold the default rollback journal.
    """
    from ogtool.store import Store

    path = tmp_path / "shared.sqlite"
    first = Store(path)
    second = Store(path)
    try:
        task = first.create_task("shared", "test", {}, mode="live", planned=1)
        second.add_result(task.id, "b", "y", "sent", "", "")
        first.add_result(task.id, "a", "x", "sent", "", "")
        assert len(first.results_for(task.id)) == 2
        mode = first._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        first.close()
        second.close()


def test_a_heartbeat_does_not_spend_the_sending_budget(tmp_path):
    """'sent' is this schema's word for success, not for a message.

    The daily sending budget used to count every successful row, so exports and
    online heartbeats ate it: 98 rows here, which is why the first real message
    of the day was refused as 'budget exhausted'.
    """
    store = Store(tmp_path / "og.sqlite")
    exporting = store.create_task("export", "export_accounts", {"a": 1}, 1)
    store.add_result(exporting.id, "acc", "row", "sent")
    online = store.create_task("online", "keep_online", {"a": 1}, 1)
    store.add_result(online.id, "acc", "beat", "sent")
    sending = store.create_task("blast", "mass_send", {"a": 1}, 1)
    store.add_result(sending.id, "acc", "@chat", "sent")

    assert store.count_sent_today("acc") == 3
    assert store.count_sent_today("acc", features=("mass_send",)) == 1
    assert store.count_sent_today("acc", features=("export_accounts",)) == 1


def test_the_plan_can_be_set_after_the_listing(tmp_path):
    """A listing learns its size by listing; the receipt must still add up."""
    store = Store(tmp_path / "og.sqlite")
    task = store.create_task("dialogs", "accounts", {"limit": 5}, "live")
    assert store.task_row(task.id)["planned"] == 0
    store.set_task_planned(task.id, 5)
    assert store.task_row(task.id)["planned"] == 5
