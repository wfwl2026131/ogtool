"""#9 batch login: session discovery, metadata, ZIP import and the logouts."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ogtool.config import ConfigError
from ogtool.features.batch_login import (
    LOGOUT_ALL,
    LOGOUT_BOTH,
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


def test_an_unclassified_account_is_not_swept_into_a_mode(ctx, capsys):
    # frozen and both name a kind; an account nobody classified belongs to
    # neither, and logging it out would be a guessing game.
    assert run(LoginConfig(logout=LOGOUT_FROZEN), ctx) == 0
    out = capsys.readouterr().out
    assert "批量退出冻结账号：0 accounts" in out
    assert "跳过 2 个非本类别" in out


def test_the_frozen_mode_picks_only_the_frozen_accounts(ctx, capsys):
    from ogtool.store import KINDS  # noqa: F401 - the vocabulary is the contract

    ctx.store.set_account_kind("acc1", "frozen")
    ctx.store.set_account_kind("acc2", "two_way")
    assert run(LoginConfig(logout=LOGOUT_FROZEN), ctx) == 0
    out = capsys.readouterr().out
    assert "批量退出冻结账号：1 accounts" in out
    assert "acc2=two_way" in out


def test_the_two_way_mode_is_the_other_bucket(ctx, capsys):
    ctx.store.set_account_kind("acc1", "frozen")
    ctx.store.set_account_kind("acc2", "two_way")
    assert run(LoginConfig(logout=LOGOUT_BOTH), ctx) == 0
    out = capsys.readouterr().out
    assert "批量退出双向号：1 accounts" in out
    assert "acc1=frozen" in out


def test_all_ignores_the_kind_column(ctx, capsys):
    ctx.store.set_account_kind("acc1", "frozen")
    ctx.store.set_account_kind("acc2", "one_way")
    assert run(LoginConfig(logout=LOGOUT_ALL), ctx) == 0
    assert "批量退出所有账号：2 accounts" in capsys.readouterr().out


def test_an_unknown_logout_mode_is_rejected(ctx, capsys):
    assert run(LoginConfig(logout="everything"), ctx) == 2
    assert "--logout wants frozen|all|both" in capsys.readouterr().out


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


def test_an_import_task_is_opened_with_the_rows_it_will_write(ctx, tmp_path: Path):
    """The task used to open with planned 0 and then record a row per session.

    The schema calls that "more rows than planned", and nothing caught it before:
    the sweep's fixture logged out instead of importing, so it imported nothing.
    """
    from ogtool import receipts

    folder = tmp_path / "incoming"
    folder.mkdir()
    (folder / "one.session").write_bytes(b"x")
    (folder / "two.session").write_bytes(b"x")
    config = LoginConfig(import_folder=str(folder))
    assert run(config, ctx) == 0
    body = json.loads(next(receipts.receipt_dir(ctx.settings)
                           .glob("batch_login-*.json")).read_text())
    assert body["planned"] == body["results"]["total"] == 2
    assert body["ok"] == 2
    assert body["unaccounted"] == 0
    assert receipts.validate(body) == []


def test_import_tdata_without_the_converter_is_a_config_error(ctx, tmp_path):
    """No opentele2 installed: say the install line, exit 2, never a traceback."""
    config = LoginConfig(import_tdata=str(tmp_path / "tdata"))
    (tmp_path / "tdata").mkdir()
    real = sys.modules.get("opentele2")
    sys.modules["opentele2"] = None  # sentinel: "present" check must fail
    sys.modules.pop("opentele2.td", None)
    try:
        import builtins
        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name.startswith("opentele2"):
                raise ModuleNotFoundError(name)
            return real_import(name, *args, **kwargs)

        builtins.__import__ = blocked
        try:
            with pytest.raises(ConfigError) as err:
                run(config, ctx)
        finally:
            builtins.__import__ = real_import
    finally:
        if real is None:
            sys.modules.pop("opentele2", None)
        else:
            sys.modules["opentele2"] = real
    assert "pip install opentele2" in str(err.value)


def test_import_tdata_converts_every_account(ctx, tmp_path, monkeypatch,
                                             capsys):
    """With the converter present, one session per tdata account is
    materialised and imported through the same pool path."""

    tdata = tmp_path / "tdata"
    tdata.mkdir()
    (tdata / "key_datas").write_bytes(b"")  # looks like a tdata folder

    fake_client = MagicMock()

    async def fake_to_telethon(session, flag, *args, **kwargs):
        Path(str(session) + ".session").write_bytes(b"fake")
        return fake_client

    async def fake_disconnect():
        return None

    fake_client.disconnect = fake_disconnect

    class FakeTDesktop:
        def __init__(self, _path):
            self.count = 2

        def isLoaded(self):
            return True

        accountsCount = property(lambda self: 2)
        ToTelethon = staticmethod(fake_to_telethon)

    fake_module = MagicMock()
    fake_module.TDesktop = FakeTDesktop
    fake_module.api = MagicMock()
    fake_module.api.UseCurrentSession = object()
    monkeypatch.setitem(sys.modules, "opentele2", fake_module)
    monkeypatch.setitem(sys.modules, "opentele2.td", fake_module)
    monkeypatch.setitem(sys.modules, "opentele2.api", fake_module)

    config = LoginConfig(import_tdata=str(tdata), label_prefix="td-")
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "converted tdata-0" in output
    assert "converted tdata-1" in output
    assert "imported td-tdata-0" in output
    assert "imported td-tdata-1" in output
    # The receipt counts one row per converted account.
    # The receipt counts one row per converted account: read the newest
    # batch_login task from the tasks table directly.
    latest = ctx.store._conn.execute(
        "SELECT id FROM tasks WHERE feature='batch_login' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    rows = ctx.store.results_for(latest["id"])
    assert len(rows) == 2


def test_import_tdata_with_no_accounts_is_a_config_error(ctx, tmp_path,
                                                         monkeypatch):
    """A folder that is not a loaded tdata tree is refused, not imported."""

    fake_module = MagicMock()

    class FakeTDesktop:
        def __init__(self, _path):
            pass

        def isLoaded(self):
            return False

    fake_module.TDesktop = FakeTDesktop
    monkeypatch.setitem(sys.modules, "opentele2", fake_module)
    monkeypatch.setitem(sys.modules, "opentele2.td", fake_module)
    monkeypatch.setitem(sys.modules, "opentele2.api", fake_module)
    config = LoginConfig(import_tdata=str(tmp_path / "not-tdata"))
    (tmp_path / "not-tdata").mkdir()
    with pytest.raises(ConfigError) as err:
        run(config, ctx)
    assert "no tdata accounts" in str(err.value)
