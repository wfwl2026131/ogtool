"""accounts dialogs: the read-only way to find a real chat for the live checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from ogtool.accounts import AccountPool


@pytest.fixture()
def pool(settings, store):
    pool = AccountPool(settings, store)
    account = pool.add_phone("+8613800000001", "acc1")
    Path(account.session).touch()
    return pool


def test_dialogs_is_read_only_without_live(pool):
    rows = pool.dialogs(["acc1"], live=False)
    assert rows
    assert rows[0][1] == "?"
    assert "dry-run" in rows[0][3]


def test_dialogs_reports_a_missing_session(pool):
    Path(pool.resolve(["acc1"])[0].session).unlink()
    rows = pool.dialogs(["acc1"], live=False)
    assert rows[0][1] == "?"
    assert "SESSION_NOT_FOUND" in rows[0][3]


def test_dialogs_live_needs_credentials(pool, settings):
    assert not settings.api_id
    rows = pool.dialogs(["acc1"], live=True)
    assert rows[0][1] == "?"
    assert rows[0][3]
