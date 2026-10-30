"""The batch-login page's "Exit Frozen Accounts" routine, sentence by sentence.

The page's own words (:1742495, :1742564-1742572): the start guard, the
collection line, the detection line, the empty result, the routine banner,
the per-account work line with "unknown phone", and the completion line.
The probe runs through @spambot exactly as ``accounts spambot`` does, so
the tests drive it with the same fake client factory; a logout still moves
the session file to the trash folder instead of unlinking it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ogtool.accounts import AccountPool


class _FakeSpamBot:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.sent: list[tuple[str, str]] = []

    def connect(self) -> None:
        return None

    def send_message(self, username: str, text: str) -> None:
        self.sent.append((username, text))

    def get_messages(self, username: str, limit: int = 5):
        return [type("M", (), {"message": self.reply})()]

    def disconnect(self) -> None:
        return None


FROZEN = "Sorry, your account is now limited until 1 January 2099."
FREE = "Good news, no limits are currently applied to your account."


@pytest.fixture()
def pool(settings, store, monkeypatch):
    monkeypatch.setenv("TG_API_ID", "12345")
    monkeypatch.setenv("TG_API_HASH", "0123456789abcdef0123456789abcdef")
    pool = AccountPool(settings, store)
    pool.add_phone("+8613800000001", "acc1")
    pool.add_phone("+8613800000002", "acc2")
    for label in ("acc1", "acc2"):
        Path(pool.resolve([label])[0].session).touch()
    return pool


def test_an_empty_pool_is_the_page_s_own_start_guard(pool, capsys):
    assert pool.exit_frozen(["nobody"]) == []
    out = capsys.readouterr().out
    assert (
        "Unable to start exit frozen accounts: task running or no accounts "
        "selected" in out
    )


def test_a_dry_run_probes_nothing_and_moves_nothing(pool, capsys):
    session = Path(pool.resolve(["acc1"])[0].session)
    rows = pool.exit_frozen(["acc1"], live=False)
    out = capsys.readouterr().out
    assert "Collecting frozen accounts" in out
    assert rows[0][1] is True
    assert "dry-run" in rows[0][2]
    assert session.exists()
    assert "Exit frozen accounts completed" not in out


def test_no_frozen_accounts_detected_when_the_probes_come_back_free(
    pool, capsys
):
    rows = pool.exit_frozen(
        ["acc1"], live=True, wait=0,
        client_factory=lambda _p, _c: _FakeSpamBot(FREE),
    )
    out = capsys.readouterr().out
    assert "No frozen accounts detected" in out
    assert all(ok for _, ok, _ in rows)
    assert Path(pool.resolve(["acc1"])[0].session).exists()


def test_a_frozen_account_is_detected_with_index_and_phone(pool, capsys):
    pool.exit_frozen(
        ["acc1"], live=True, wait=0,
        client_factory=lambda _p, _c: _FakeSpamBot(FROZEN),
    )
    out = capsys.readouterr().out
    assert "Detected frozen account #1 (index 0)" in out
    assert "Starting exit frozen accounts routine (1 accounts)" in out
    assert "Processing frozen account #1 (index 0, phone +8613800000001)" in out
    assert "Exit frozen accounts completed" in out


def test_a_frozen_account_without_a_phone_says_unknown_phone(
    pool, capsys, store
):
    from ogtool.store import Account

    store.add_account(
        Account("nophone", session=str(Path(pool.resolve(["acc2"])[0].session)))
    )
    pool.exit_frozen(
        ["nophone"], live=True, wait=0,
        client_factory=lambda _p, _c: _FakeSpamBot(FROZEN),
    )
    out = capsys.readouterr().out
    assert "phone unknown phone" in out


def test_the_frozen_session_is_moved_to_the_trash(pool, tmp_path, capsys):
    session = Path(pool.resolve(["acc1"])[0].session)
    trash = tmp_path / "trash"
    rows = pool.exit_frozen(
        ["acc1"], live=True, wait=0, trash=str(trash),
        client_factory=lambda _p, _c: _FakeSpamBot(FROZEN),
    )
    assert not session.exists()
    assert (trash / session.name).exists()
    assert rows[0][1] is True
    assert "logged out" in rows[0][2]
    assert "Exit frozen accounts completed" in capsys.readouterr().out


def test_a_free_account_never_reaches_the_routine(pool, tmp_path):
    session = Path(pool.resolve(["acc1"])[0].session)
    trash = tmp_path / "trash"
    pool.exit_frozen(
        ["acc1"], live=True, wait=0, trash=str(trash),
        client_factory=lambda _p, _c: _FakeSpamBot(FREE),
    )
    assert session.exists()
    assert not trash.exists() or not any(trash.iterdir())


def test_every_frozen_account_is_processed_in_order(pool, capsys):
    rows = pool.exit_frozen(
        ["acc1", "acc2"], live=True, wait=0,
        client_factory=lambda _p, _c: _FakeSpamBot(FROZEN),
    )
    out = capsys.readouterr().out
    assert "Detected frozen account #1 (index 0)" in out
    assert "Detected frozen account #2 (index 1)" in out
    assert "Starting exit frozen accounts routine (2 accounts)" in out
    assert len(rows) == 2
    assert all(ok for _, ok, _ in rows)
