"""#22 advanced: the account report, the session archive and the tdata note."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ogtool.config import ConfigError
from ogtool.features import export_accounts
from ogtool.features.export_accounts import (
    FIELDS,
    NOT_IMPLEMENTED,
    ExportConfig,
    build_rows,
    parse_profiles,
    run,
    write_csv,
    write_json,
)
from ogtool.store import Account, Profile


def config_for(tmp_path: Path, **overrides) -> ExportConfig:
    values = {
        "account_labels": [],
        "output_dir": str(tmp_path / "out"),
        "profiles": {},
        "copy_sessions": True,
        "formats": ("csv", "json"),
    }
    values.update(overrides)
    return ExportConfig(**values)


def test_the_report_has_the_documented_columns(tmp_path, ctx, capsys):
    assert run(config_for(tmp_path), ctx) == 0
    out = tmp_path / "out"
    lines = (out / "accounts.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0].split(",") == list(FIELDS)
    assert len(lines) == 3  # the header plus the two pooled accounts
    assert lines[1].split(",")[0] == "acc1"
    assert "PLAN: 2 accounts, 2 files" in capsys.readouterr().out


def test_the_phone_is_masked_in_both_files(tmp_path, ctx):
    store = ctx.store
    store.add_account(Account("phone1", phone="+15551230001"))
    config = config_for(tmp_path, account_labels=["phone1"])
    # A second label covers the account the pool already holds, so the export
    # is just this one row.
    config.account_labels = ["phone1"]
    run(config, ctx)
    csv_row = (tmp_path / "out" / "accounts.csv").read_text(
        encoding="utf-8").splitlines()[1]
    assert "+1555***0001" in csv_row
    payload = json.loads((tmp_path / "out" / "accounts.json").read_text(
        encoding="utf-8"))
    assert payload["accounts"][0]["phone"] == "+1555***0001"


def test_short_numbers_stay_as_they_are():
    account = Account("a", phone="12345")
    rows = build_rows([account], [], {})
    assert rows[0]["phone"] == "12345"
    assert build_rows([Account("b")], [], {})[0]["phone"] == "-"


def test_the_json_carries_the_tdata_note(tmp_path, ctx):
    run(config_for(tmp_path), ctx)
    payload = json.loads((tmp_path / "out" / "accounts.json").read_text(
        encoding="utf-8"))
    assert payload["not_implemented"] == NOT_IMPLEMENTED
    assert "tdata" in payload["not_implemented"]
    assert len(payload["accounts"]) == 2


def test_the_simulated_profile_wins_over_the_pool(tmp_path, ctx, capsys):
    ctx.store.save_profile(Profile("acc1", user_id="42", username="ann",
                                   device="iPhone 15", twofa="on"))
    config = config_for(tmp_path, profiles={"acc2": Profile(
        "acc2", user_id="7", username="bob", device="Pixel 8", twofa="off")})
    run(config, ctx)
    lines = (tmp_path / "out" / "accounts.csv").read_text(
        encoding="utf-8").splitlines()
    assert "42,ann" in lines[1]
    assert ",7,bob," in lines[2]
    output = capsys.readouterr().out
    assert "acc1\t42" in output
    assert "acc2\t7" in output


def test_a_missing_session_file_is_reported_and_skipped(tmp_path, ctx, capsys):
    ctx.store.add_account(Account("gone", session=str(tmp_path / "nope.session")))
    run(config_for(tmp_path, copy_sessions=True), ctx)
    output = capsys.readouterr().out
    assert "session: gone -> missing" in output
    # The two pooled sessions still copy; the missing one does not.
    assert "archived: 2 session files" in output
    assert not (tmp_path / "out" / "nope.session").exists()


def test_no_sessions_means_no_archive_folder(tmp_path, ctx, capsys):
    run(config_for(tmp_path, copy_sessions=False), ctx)
    assert not (tmp_path / "out" / "sessions").exists()
    assert "sessions: skip" in capsys.readouterr().out


def test_csv_only_writes_no_json(tmp_path, ctx):
    run(config_for(tmp_path, formats=("csv",)), ctx)
    assert (tmp_path / "out" / "accounts.csv").exists()
    assert not (tmp_path / "out" / "accounts.json").exists()


def test_an_unknown_format_is_a_config_error(settings):
    with pytest.raises(ConfigError):
        ExportConfig.from_dict(
            {"export_accounts": {"formats": "csv,pdf"}}, settings)


def test_profiles_parse_five_fields_and_pad_the_rest():
    parsed = parse_profiles(["ann|1|@ann|iPhone 15", "bob||"])
    assert parsed["ann"].device == "iPhone 15"
    assert parsed["ann"].twofa == ""
    assert parsed["bob"].user_id == ""
    assert parse_profiles([""]) == {}


def test_the_helpers_report_how_many_rows_they_wrote(tmp_path):
    rows = [{"label": "a", "phone": "1,2"}]
    assert write_csv(tmp_path / "x.csv", rows) == 1
    assert '"1,2"' in (tmp_path / "x.csv").read_text(encoding="utf-8")
    assert write_json(tmp_path / "x.json", rows, {"note": "n"}) == 1
    payload = json.loads((tmp_path / "x.json").read_text(encoding="utf-8"))
    assert payload["note"] == "n"


def test_the_module_is_registered():
    assert export_accounts.FEATURE == "export_accounts"
    assert export_accounts.TITLE == "#22 高级功能"
