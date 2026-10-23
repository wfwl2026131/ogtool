"""#9 batch login: session discovery, metadata, ZIP import and the logouts."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from ogtool.config import ConfigError
from ogtool.features.batch_login import (
    LOGOUT_ALL,
    LOGOUT_FROZEN,
    LoginConfig,
    find_sessions,
    read_metadata,
    run,
)


def test_find_sessions_skips_journals(tmp_path: Path):
    (tmp_path / "a.session").write_bytes(b"")
    (tmp_path / "a.session-journal").write_bytes(b"")
    (tmp_path / "b.session").write_bytes(b"")
    files = find_sessions(tmp_path)
    assert [path.name for path in files] == ["a.session", "b.session"]


def test_find_sessions_rejects_a_missing_folder(tmp_path: Path):
    with pytest.raises(ConfigError):
        find_sessions(tmp_path / "nope")


def test_read_metadata_reads_the_sidecar(tmp_path: Path):
    session = tmp_path / "a.session"
    session.write_bytes(b"")
    session.with_suffix(".json").write_text(
        json.dumps({"id": 42, "phone": "+1555", "username": "demo"}))
    meta = read_metadata(session)
    assert meta["id"] == "42" and meta["phone"] == "+1555"


def test_read_metadata_tolerates_a_broken_sidecar(tmp_path: Path):
    session = tmp_path / "a.session"
    session.write_bytes(b"")
    session.with_suffix(".json").write_text("{not json")
    assert read_metadata(session) == {}


def test_import_folder_registers_every_session(ctx, tmp_path: Path, capsys):
    source = tmp_path / "bundle"
    source.mkdir()
    (source / "one.session").write_bytes(b"")
    (source / "two.session").write_bytes(b"")
    (source / "two.json").write_text(json.dumps({"phone": "+15550000002"}))
    config = LoginConfig(import_folder=str(source), label_prefix="imp-")
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "imported imp-one" in output
    assert "imported imp-two phone=+15550000002" in output
    assert "账号数：4" in output


def test_import_zip_reads_the_same_layout(ctx, tmp_path: Path, capsys):
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("deep/three.session", b"")
    config = LoginConfig(import_zip=str(archive))
    assert run(config, ctx) == 0
    assert "imported three" in capsys.readouterr().out


def test_export_ids_writes_one_row_per_account(ctx, tmp_path: Path, capsys):
    export = tmp_path / "ids.txt"
    config = LoginConfig(import_folder="", export_ids=str(export))
    config.import_folder = ""
    run(LoginConfig(export_ids=str(export)), ctx)
    body = export.read_text()
    assert "label\tid\tphone" in body
    assert "acc1" in body


def test_logout_modes_are_named(ctx, capsys):
    assert run(LoginConfig(logout=LOGOUT_ALL), ctx) == 0
    assert "批量退出所有账号：2 accounts" in capsys.readouterr().out


def test_unknown_logout_mode_is_rejected(ctx, capsys):
    assert run(LoginConfig(logout=LOGOUT_FROZEN), ctx) == 0
    assert "批量退出冻结账号：2 accounts" in capsys.readouterr().out


def test_nothing_to_do_is_a_config_error():
    with pytest.raises(ConfigError):
        LoginConfig.from_dict({}, None)  # type: ignore[arg-type]


class LogoutSender:
    """Stands in for the live transport: records which accounts were logged out."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def logout(self, label: str):
        from ogtool.senders import SendResult

        self.calls.append(label)
        return SendResult(ok=True)

    def close(self) -> None:
        pass


def test_a_dry_run_logout_only_prints(ctx, capsys):
    assert run(LoginConfig(logout=LOGOUT_ALL), ctx) == 0
    output = capsys.readouterr().out
    assert "logout all acc1" in output
    assert "dry-run: nothing was logged out" in output


def test_a_live_logout_really_calls_the_client(ctx, live):
    sender = LogoutSender()
    live(sender)
    assert run(LoginConfig(logout=LOGOUT_ALL), ctx) == 0
    assert sender.calls == ["acc1", "acc2"], "the logouts were only printed"
    statuses = {account.label: account.status for account in ctx.store.list_accounts()}
    assert statuses["acc1"] == "logged-out"
