"""The tdata creation round: official directories for selected accounts.

The page's own lines (:1744590-1744615): the directory refusals, the
preparation line, the two closing verdicts, and the empty-pool refusal.
The empty maps fallback (:1737483) is what makes a fresh directory valid.
"""

from __future__ import annotations

from ogtool import cli
from ogtool.accounts import AccountPool, prepare_tdata_dirs


def _pool(settings, store, monkeypatch, label="acc1", phone="+8613800000007"):
    monkeypatch.setenv("TG_API_ID", "12345")
    monkeypatch.setenv("TG_API_HASH", "0123456789abcdef0123456789abcdef")
    pool = AccountPool(settings, store)
    pool.add_phone(phone, label)
    return pool


def test_a_dry_run_plans_without_touching_the_disk(
    settings, store, monkeypatch, tmp_path
):
    _pool(settings, store, monkeypatch)
    rows = prepare_tdata_dirs(
        AccountPool(settings, store), ["acc1"], tmp_path, live=False
    )
    assert rows == [
        ("acc1", True, f"dry-run: would prepare tdata at {tmp_path / 'acc1'}")
    ]
    assert not (tmp_path / "acc1").exists()


def test_a_live_run_prepares_the_directory_and_the_maps(
    settings, store, monkeypatch, tmp_path
):
    _pool(settings, store, monkeypatch)
    rows = prepare_tdata_dirs(
        AccountPool(settings, store), ["acc1"], tmp_path, live=True
    )
    assert rows[0][1] is True
    assert (tmp_path / "acc1" / "maps").is_file()


def test_an_existing_directory_is_a_refusal(settings, store, monkeypatch, tmp_path):
    _pool(settings, store, monkeypatch)
    (tmp_path / "acc1").mkdir()
    rows = prepare_tdata_dirs(
        AccountPool(settings, store), ["acc1"], tmp_path, live=True
    )
    assert rows[0][1] is False
    assert rows[0][2].startswith("Failed to clear existing directory:")


def test_the_cli_names_the_refusals(settings, store, monkeypatch, tmp_path, capsys):
    _pool(settings, store, monkeypatch)
    code = cli.run(
        [
            "--config",
            str(settings.path),
            "accounts",
            "tdata-prepare",
            "--accounts",
            "acc1",
            "--out-dir",
            str(tmp_path),
            "--live",
            "--allow-write",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Create tdata for Selected Accounts" in out
    assert "Directory preparation completed." in out
    assert (tmp_path / "acc1" / "maps").is_file()


def test_the_cli_refuses_live_without_write(settings, store, monkeypatch, capsys):
    _pool(settings, store, monkeypatch)
    code = cli.run(
        [
            "--config",
            str(settings.path),
            "accounts",
            "tdata-prepare",
            "--accounts",
            "acc1",
            "--live",
        ]
    )
    out = capsys.readouterr().out
    assert code == 2
    assert "--allow-write" in out


def test_an_empty_pool_is_the_page_refusal(settings, capsys):
    code = cli.run(
        [
            "--config",
            str(settings.path),
            "accounts",
            "tdata-prepare",
            "--live",
            "--allow-write",
        ]
    )
    out = capsys.readouterr().out
    assert code == 2
    assert "No eligible accounts for creation." in out


def test_a_failure_names_the_closing_verdict(
    settings, store, monkeypatch, tmp_path, capsys
):
    _pool(settings, store, monkeypatch)
    (tmp_path / "acc1").mkdir()
    code = cli.run(
        [
            "--config",
            str(settings.path),
            "accounts",
            "tdata-prepare",
            "--accounts",
            "acc1",
            "--out-dir",
            str(tmp_path),
            "--live",
            "--allow-write",
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "Some accounts failed during preparation." in out
