"""Batch modify cloud passwords: the console tool, its refusals, its silence.

The reference's own outcomes (1737298-1737305): "Account %1 modify successfully
via %2", "Account %1 password updated and metadata synced", and the closing line
"Password change task finished. Success: %1/%2".

The one rule that matters more than the behaviour: the password itself never
reaches stdout, a log file, or a task row.
"""

from __future__ import annotations

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


def test_a_dry_run_says_what_it_would_do_and_changes_nothing(pool):
    rows = pool.passwords(["acc1"], "s3cret", live=False)
    assert rows == [("acc1", True, "dry-run: would change the cloud password")]


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


def test_a_live_run_uses_edit_2fa_and_reports_the_reference_sentence(pool):
    used = {}

    class FakeClient:
        def connect(self):
            used["connected"] = True

        def edit_2fa(self, current_password=None, new_password=None):
            used["new"] = new_password
            used["current"] = current_password

        def disconnect(self):
            used["disconnected"] = True

    rows = pool.passwords(["acc1"], "s3cret", live=True,
                          client_factory=lambda *a, **k: FakeClient(),
                          current_password="old-one")
    assert used == {"connected": True, "new": "s3cret",
                    "current": "old-one", "disconnected": True}
    assert rows[0][1] is True
    assert rows[0][2] == "Account acc1 modify successfully via 2fa"


def test_a_failed_live_change_is_a_row_not_a_crash(pool):
    class Angry:
        def connect(self):
            pass

        def edit_2fa(self, **kwargs):
            raise RuntimeError("SESSION_PASSWORD_NEEDED")

        def disconnect(self):
            pass

    rows = pool.passwords(["acc1"], "s3cret", live=True,
                          client_factory=lambda *a, **k: Angry())
    assert rows[0][1] is False
    assert "SESSION_PASSWORD_NEEDED" in rows[0][2]


def test_the_secret_is_read_from_a_file_never_a_flag(tmp_path, monkeypatch):
    from ogtool import cli

    path = tmp_path / "new.txt"
    path.write_text("from-a-file\n", encoding="utf-8")
    assert cli._read_secret(str(path)) == "from-a-file"
    assert cli._read_secret(None) == ""
    with pytest.raises(OSError):
        cli._read_secret(str(tmp_path / "missing.txt"))


def test_the_closing_line_is_the_reference_sentence(capsys, settings, store,
                                                    tmp_path, monkeypatch):
    from ogtool import cli

    pool = AccountPool(settings, store)
    pool.add_phone("+8613800000009", "acc1")
    Path(pool.resolve(["acc1"])[0].session).touch()
    monkeypatch.setattr(cli, "_Pool", lambda *a, **k: pool)
    path = tmp_path / "new.txt"
    path.write_text("pw\n", encoding="utf-8")
    code = cli.run(["accounts", "password", "--accounts", "acc1",
                    "--new-password-file", str(path)])
    output = capsys.readouterr().out
    assert "Password change task finished. Success: 1/1" in output
    assert "pw" not in output.replace(path.name, "")
    assert code == 0
