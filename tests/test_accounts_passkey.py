"""The password page's second task: metadata passkey export.

The reference's own lines (:1737294, :1737356-1737385): the export banner,
"Exported metadata passkey file for account %1 to %2", the no-phone skip, the
directory failure, and the closing "Passkey export finished. Success: %1/%2".
The build's boundary is the reference's own: metadata only, because the
private key is not available in this build.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ogtool.accounts import AccountPool


@pytest.fixture()
def pool(settings, store, monkeypatch):
    monkeypatch.setenv("TG_API_ID", "12345")
    monkeypatch.setenv("TG_API_HASH", "0123456789abcdef0123456789abcdef")
    pool = AccountPool(settings, store)
    for index, label in enumerate(("acc1", "acc2"), start=1):
        pool.add_phone(f"+861380000000{index}", label)
        Path(pool.resolve([label])[0].session).touch()
    return pool


def test_a_dry_run_plans_one_row_per_account(pool):
    rows = pool.passkeys(["acc1"], "nowhere", live=False)
    assert rows == [("acc1", True, "dry-run: would export a metadata passkey file")]


def test_a_live_export_writes_one_metadata_file(pool, tmp_path):
    out = tmp_path / "passkeys"
    rows = pool.passkeys(["acc1"], out, live=True)
    assert rows[0][1] is True
    assert rows[0][2].startswith("Exported metadata passkey file for account acc1 to ")
    files = list(out.glob("passkeyacc1_*.passkey"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["phone"] == "+8613800000001"
    assert payload["label"] == "acc1"
    assert payload["kind"] == "metadata"


def test_an_account_without_a_phone_is_skipped_whole(pool, tmp_path, store):
    from ogtool.store import Account

    store.add_account(
        Account(label="bare", phone="", session=str(tmp_path / "bare.session"))
    )
    rows = pool.passkeys(["bare"], tmp_path, live=True)
    assert rows == [("bare", False, "Account bare has no phone number, skipped.")]


def test_a_dry_run_needs_no_folder_yet(pool):
    """The live run creates the folder; a plan never refuses over it."""
    rows = pool.passkeys(["acc1"], "no/such/folder", live=False)
    assert rows == [("acc1", True, "dry-run: would export a metadata passkey file")]


def test_the_export_folder_is_created_for_a_live_run(pool, tmp_path):
    out = tmp_path / "deep" / "passkeys"
    rows = pool.passkeys(["acc1"], out, live=True)
    assert rows[0][1] is True
    assert list(out.glob("*.passkey"))


def test_an_unwritable_folder_speaks_the_page_sentence(pool, tmp_path, store):

    # A folder path that is really a file: the mkdir cannot succeed.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a folder", encoding="utf-8")
    pool.add_phone("+8613800000009", "acc9")
    rows = pool.passkeys(["acc9"], blocker / "sub", live=True)
    assert rows == [
        (
            "acc9",
            False,
            "Failed to create passkey directory: "
            f"[Errno 20] Not a directory: '{blocker}/sub'",
        )
    ]


def test_the_closing_line_is_the_reference_sentence(
    capsys, settings, store, tmp_path, monkeypatch
):
    from ogtool import cli

    pool = AccountPool(settings, store)
    pool.add_phone("+8613800000007", "acc1")
    monkeypatch.setattr(cli, "_Pool", lambda *a, **k: pool)
    out = tmp_path / "passkeys"
    code = cli.run(
        [
            "--config",
            str(settings.path),
            "accounts",
            "passkey",
            "--accounts",
            "acc1",
            "--out-dir",
            str(out),
            "--live",
            "--allow-write",
        ]
    )
    output = capsys.readouterr().out
    assert "Passkey export task started." in output
    assert (
        "System WebAuthn is unavailable in this build; "
        "exporting metadata-only passkey files."
    ) in output
    assert "Passkey export finished. Success: 1/1" in output
    assert code == 0
    assert list(out.glob("passkeyacc1_*.passkey"))


def test_an_empty_pool_is_the_page_refusal(capsys, settings, store):
    from ogtool import cli

    code = cli.run(
        [
            "--config",
            str(settings.path),
            "accounts",
            "passkey",
            "--live",
            "--allow-write",
        ]
    )
    out = capsys.readouterr().out
    assert "Please select at least one account." in out
    assert code == 2


def test_a_dry_run_prints_no_banners(capsys, settings, store, monkeypatch):
    from ogtool import cli

    pool = AccountPool(settings, store)
    pool.add_phone("+8613800000008", "acc1")
    monkeypatch.setattr(cli, "_Pool", lambda *a, **k: pool)
    code = cli.run(
        ["--config", str(settings.path), "accounts", "passkey", "--accounts", "acc1"]
    )
    out = capsys.readouterr().out
    assert "Passkey export task started." not in out
    assert "Passkey export finished. Success: 1/1" in out
    assert code == 0
