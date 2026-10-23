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
