"""Batch modify cloud passwords: the console tool, its refusals, its silence.

The reference's own outcomes (:1737296-1737305): "Account %1 modify
successfully via %2" names the old-password source that answered, the sync
verdicts follow, the failures speak the page's own sentences, and the closing
line is "Password change task finished. Success: %1/%2".

The one rule that matters more than the behaviour: the password itself never
reaches stdout, a log file, or a task row.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ogtool.accounts import AccountPool


@pytest.fixture()
def pool(settings, store, monkeypatch):
    # A live path needs credentials to open a client; the values are fake and
    # nothing here reaches the network.
    monkeypatch.setenv("TG_API_ID", "12345")
    monkeypatch.setenv("TG_API_HASH", "0123456789abcdef0123456789abcdef")
    pool = AccountPool(settings, store)
    for index, label in enumerate(("acc1", "acc2"), start=1):
        pool.add_phone(f"+861380000000{index}", label)
        # ``add_phone`` only records the path; the method refuses an account
        # whose session file is not there, so the fixture creates it.
        Path(pool.resolve([label])[0].session).touch()
    return pool


class FakeClient:
    """Records every edit_2fa call; the verdict is whatever the test mounts."""

    def __init__(self, calls: list, fail_on=None, stop_at=None):
        self._calls = calls
        self._fail_on = fail_on or frozenset()
        self._stop_at = stop_at

    def connect(self):
        self._calls.append({"op": "connect"})

    def edit_2fa(self, current_password=None, new_password=None):
        call = {"current": current_password, "new": new_password}
        self._calls.append(call)
        if call["current"] in self._fail_on:
            raise RuntimeError("PASSWORD_HASH_INVALID")
        if self._stop_at is not None and len(self._calls) >= self._stop_at:
            raise KeyboardInterrupt()

    def disconnect(self):
        self._calls.append({"op": "disconnect"})


def test_a_dry_run_says_what_it_would_do_and_changes_nothing(pool):
    rows = pool.passwords(["acc1"], "s3cret", live=False)
    assert rows == [
        (
            "acc1",
            True,
            "dry-run: would change the cloud password (via empty old password)",
        )
    ]


def test_without_a_password_every_selected_account_says_so(pool):
    rows = pool.passwords(["acc1", "acc2"], "", live=False)
    assert [row[0] for row in rows] == ["acc1", "acc2"]
    assert all(not ok for _, ok, _ in rows)
    assert all("no new password" in detail for _, _, detail in rows)


def test_a_missing_session_is_reported_not_skipped(pool, settings, store):
    pool.add_phone("+8613800000003", "gone")
    rows = pool.passwords(["gone"], "s3cret", live=False)
    assert rows[0][0] == "gone"
    assert rows[0][1] is False
    assert "SESSION_NOT_FOUND" in rows[0][2]


def test_the_password_never_appears_in_a_row(pool):
    rows = pool.passwords(["acc1"], "hunter2-very-secret", live=False)
    assert "hunter2" not in repr(rows)


def test_the_box_default_is_the_first_source(pool):
    calls: list = []
    rows = pool.passwords(
        ["acc1"],
        "s3cret",
        live=True,
        client_factory=lambda *a, **k: FakeClient(calls),
        current_password="old-one",
    )
    assert calls[1] == {"current": "old-one", "new": "s3cret"}
    assert rows[0][1] is True
    assert rows[0][2] == ("Account acc1 modify successfully via input old password")


def test_a_recorded_metadata_password_answers_when_the_box_is_empty(pool, store):
    calls: list = []
    store.set_account_password("acc1", "recorded-old")
    rows = pool.passwords(
        ["acc1"], "s3cret", live=True, client_factory=lambda *a, **k: FakeClient(calls)
    )
    assert calls[1] == {"current": "recorded-old", "new": "s3cret"}
    assert rows[0][2] == ("Account acc1 modify successfully via metadata old password")


def test_the_box_default_beats_a_stale_metadata_password(pool, store):
    calls: list = []
    store.set_account_password("acc1", "stale-old")
    rows = pool.passwords(
        ["acc1"],
        "s3cret",
        live=True,
        client_factory=lambda *a, **k: FakeClient(calls),
        current_password="fresh-old",
    )
    assert calls[1]["current"] == "fresh-old"
    assert "via input old password" in rows[0][2]


def test_the_empty_source_tries_no_current_password(pool):
    calls: list = []
    rows = pool.passwords(
        ["acc1"], "s3cret", live=True, client_factory=lambda *a, **k: FakeClient(calls)
    )
    assert calls[1] == {"current": None, "new": "s3cret"}
    assert "via empty old password" in rows[0][2]


def test_a_successful_change_syncs_the_metadata(pool, store, capsys):
    calls: list = []
    rows = pool.passwords(
        ["acc1"], "s3cret", live=True, client_factory=lambda *a, **k: FakeClient(calls)
    )
    assert store.account_password("acc1") == "s3cret"
    out = capsys.readouterr().out
    assert "Account acc1 password updated and metadata synced." in out
    assert rows[0][1] is True


def test_a_sidecar_json_next_to_the_session_is_synced_too(pool, capsys):
    calls: list = []
    sidecar = Path(pool.resolve(["acc1"])[0].session).with_suffix(".json")
    sidecar.write_text(json.dumps({"phone": "+8613800000001"}), encoding="utf-8")
    pool.passwords(
        ["acc1"], "s3cret", live=True, client_factory=lambda *a, **k: FakeClient(calls)
    )
    out = capsys.readouterr().out
    assert "Account acc1 password updated and JSON synced." in out
    synced = json.loads(sidecar.read_text(encoding="utf-8"))
    assert synced["phone"] == "+8613800000001"
    assert synced["password"] == "s3cret"


def test_a_failed_store_write_skips_the_metadata_sync(pool, store, capsys, monkeypatch):
    calls: list = []

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "set_account_password", broken)
    rows = pool.passwords(
        ["acc1"], "s3cret", live=True, client_factory=lambda *a, **k: FakeClient(calls)
    )
    out = capsys.readouterr().out
    assert "Account acc1 password updated (metadata sync skipped)." in out
    assert rows[0][1] is True


def test_a_failed_live_change_speaks_the_page_sentences(pool):
    calls: list = []
    rows = pool.passwords(
        ["acc1"],
        "s3cret",
        live=True,
        client_factory=lambda *a, **k: FakeClient(calls, fail_on={None}),
    )
    assert rows[0][1] is False
    detail = rows[0][2]
    assert "Account acc1 failed to change password." in detail
    assert (
        "Account acc1 (+8613800000001) error: RuntimeError: PASSWORD_HASH_INVALID"
    ) in detail


def test_a_stop_midway_gives_back_what_it_finished(pool, capsys):
    calls: list = []
    rows = pool.passwords(
        ["acc1", "acc2"],
        "s3cret",
        live=True,
        client_factory=lambda *a, **k: FakeClient(calls, stop_at=5),
    )
    # acc1 finished (connect, edit, disconnect); acc2's edit raises the stop,
    # so only acc1 is in the rows and the page's own line is out. The CLI
    # closes against the whole plan, so the receipt stays honest.
    assert [row[0] for row in rows] == ["acc1"]
    assert "Stop requested..." in capsys.readouterr().out


def test_the_secret_is_read_from_a_file_never_a_flag(tmp_path, monkeypatch):
    from ogtool import cli

    path = tmp_path / "new.txt"
    path.write_text("from-a-file\n", encoding="utf-8")
    assert cli._read_secret(str(path)) == "from-a-file"
    assert cli._read_secret(None) == ""
    with pytest.raises(OSError):
        cli._read_secret(str(tmp_path / "missing.txt"))


def test_the_closing_line_is_the_reference_sentence(
    capsys, settings, store, tmp_path, monkeypatch
):
    from ogtool import cli

    pool = AccountPool(settings, store)
    pool.add_phone("+8613800000009", "acc1")
    Path(pool.resolve(["acc1"])[0].session).touch()
    monkeypatch.setattr(cli, "_Pool", lambda *a, **k: pool)
    path = tmp_path / "new.txt"
    path.write_text("pw\n", encoding="utf-8")
    # The config is the fixture's, not whatever a repo checkout happens to
    # hold: without it the run looked for ./config.yaml and a CI runner (no
    # config on disk) failed a test that never needed a real account.
    code = cli.run(
        [
            "--config",
            str(settings.path),
            "accounts",
            "password",
            "--accounts",
            "acc1",
            "--new-password-file",
            str(path),
        ]
    )
    output = capsys.readouterr().out
    assert "Password change task finished. Success: 1/1" in output
    assert "pw" not in output.replace(path.name, "")
    assert code == 0


def test_a_live_run_refuses_while_a_recent_password_task_is_running(
    capsys, settings, store, tmp_path
):
    from ogtool import cli

    store.create_task("password", "accounts", {"live": True}, mode="live")
    secret = tmp_path / "new.txt"
    secret.write_text("pw\n", encoding="utf-8")
    code = cli.run(
        [
            "--config",
            str(settings.path),
            "accounts",
            "password",
            "--live",
            "--allow-write",
            "--new-password-file",
            str(secret),
        ]
    )
    out = capsys.readouterr().out
    assert "Password change task is running, please stop it first." in out
    assert code == 2


def test_the_password_column_survives_an_older_database(tmp_path):
    import sqlite3

    from ogtool.store import Store

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE accounts (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               label TEXT NOT NULL UNIQUE,
               phone TEXT NOT NULL DEFAULT '',
               session TEXT NOT NULL DEFAULT '',
               proxy TEXT NOT NULL DEFAULT '',
               status TEXT NOT NULL DEFAULT 'new',
               added_at TEXT NOT NULL,
               last_seen TEXT NOT NULL DEFAULT '',
               notes TEXT NOT NULL DEFAULT '')"""
    )
    conn.execute(
        "INSERT INTO accounts (label, phone, added_at) VALUES ('acc1', '', '')"
    )
    conn.commit()
    conn.close()
    store = Store(str(path))
    store.set_account_password("acc1", "s3cret")
    assert store.account_password("acc1") == "s3cret"
    assert store.account_password("nobody") == ""


def test_readding_an_account_keeps_its_recorded_password(pool, store):
    store.set_account_password("acc1", "s3cret")
    pool.add_phone("+8613800000001", "acc1", notes="refreshed")
    assert store.account_password("acc1") == "s3cret"
