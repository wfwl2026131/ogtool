"""Batch logout: the console tool, and the one place this rebuild differs.

The reference has four lines (1858834-1858838): "Batch logout already running",
"Batch logout completed", "Batch logout is not available", "Batch logout
already running".

A logout destroys the only copy of the login, so this rebuild MOVES the session
file to a trash directory instead of unlinking it, and prints where it went.
The difference is deliberate and reported, not hidden.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ogtool.accounts import AccountPool


@pytest.fixture()
def pool(settings, store):
    pool = AccountPool(settings, store)
    pool.add_phone("+8613800000001", "acc1")
    pool.add_phone("+8613800000002", "acc2")
    for label in ("acc1", "acc2"):
        Path(pool.resolve([label])[0].session).touch()
    return pool


def test_a_dry_run_moves_nothing(pool):
    session = Path(pool.resolve(["acc1"])[0].session)
    rows = pool.logout(["acc1"], live=False)
    assert rows[0][1] is True
    assert "would log out" in rows[0][2]
    assert session.exists()


def test_a_live_logout_moves_the_session_instead_of_deleting_it(pool, tmp_path):
    session = Path(pool.resolve(["acc1"])[0].session)
    rows = pool.logout(["acc1"], live=True, trash=str(tmp_path / "trash"))
    moved = tmp_path / "trash" / session.name
    assert not session.exists()
    assert moved.exists(), "the session must still exist somewhere"
    assert "Batch logout completed" in rows[0][2]
    assert str(moved) in rows[0][2]


def test_every_selected_account_is_logged_out(pool, tmp_path):
    rows = pool.logout(["acc1", "acc2"], live=True, trash=str(tmp_path))
    assert [row[0] for row in rows] == ["acc1", "acc2"]
    assert all(ok for _, ok, _ in rows)
    assert sorted(path.name for path in tmp_path.glob("*.session")) == [
        "acc1.session", "acc2.session"]


def test_a_missing_session_is_reported_not_skipped(pool, tmp_path):
    pool.add_phone("+8613800000003", "gone")
    rows = pool.logout(["gone"], live=True, trash=str(tmp_path))
    assert rows[0][1] is False
    assert "SESSION_NOT_FOUND" in rows[0][2]


def test_a_second_logout_does_not_overwrite_the_first_copy(pool, tmp_path):
    pool.logout(["acc1"], live=True, trash=str(tmp_path))
    # Put a fresh session back and log out again into the same trash directory.
    Path(pool.resolve(["acc1"])[0].session).touch()
    rows = pool.logout(["acc1"], live=True, trash=str(tmp_path))
    assert rows[0][1] is True
    assert len(list(tmp_path.glob("acc1*.session"))) == 2


def test_the_default_trash_lives_beside_the_sessions(settings, pool, tmp_path,
                                                     monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    rows = pool.logout(["acc1"], live=True)
    assert (tmp_path / "sessions" / "trash" / "acc1.session").exists()
    assert rows[0][1] is True
