"""#24 settings: the global knobs, their defaults, their checks, the CLI page."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ogtool.cli import run
from ogtool.config import ConfigError, GlobalConfig, Settings
from ogtool.store import Account, Store

BASE = """\
api_id: 0
api_hash: ''
session_dir: data/sessions
db: data/og.sqlite
logs: data/logs
"""


def load(tmp_path: Path, extra: str = "") -> Settings:
    config = tmp_path / "config.yaml"
    config.write_text(BASE + extra, encoding="utf-8")
    return Settings.load(config)


def test_the_defaults_match_the_reference_page(tmp_path):
    runtime = load(tmp_path).runtime
    assert runtime.account_batch == 20
    assert runtime.threads_max == 128
    assert runtime.resume_delay == 0.0
    assert runtime.tp_overload == 0
    assert runtime.restart_interval == 0.0
    assert runtime.restart_timed is False


def test_the_globals_section_is_read(tmp_path):
    runtime = load(tmp_path, """\
globals:
  account_batch: 5
  threads_max: 16
  resume_delay: 2.5
  tp_overload: 10
  restart_interval: 3600
  restart_timed: true
""").runtime
    assert (runtime.account_batch, runtime.threads_max) == (5, 16)
    assert runtime.resume_delay == 2.5
    assert runtime.tp_overload == 10
    assert runtime.restart_interval == 3600
    assert runtime.restart_timed is True


def test_a_global_that_is_not_a_mapping_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load(tmp_path, "globals:\n  - 1\n")


@pytest.mark.parametrize("name,value", [
    ("account_batch", "many"),
    ("threads_max", "1.5"),
    ("resume_delay", "soon"),
    ("tp_overload", "2.5"),
    ("restart_interval", "-1"),
])
def test_a_bad_global_is_a_config_error(tmp_path, name, value):
    with pytest.raises(ConfigError) as caught:
        load(tmp_path, f"globals:\n  {name}: {value}\n")
    assert name in str(caught.value)


def test_zero_threads_and_zero_batch_are_refused(tmp_path):
    with pytest.raises(ConfigError):
        load(tmp_path, "globals:\n  threads_max: 0\n")
    with pytest.raises(ConfigError):
        load(tmp_path, "globals:\n  account_batch: 0\n")


def test_clamp_threads_never_passes_the_pool():
    runtime = GlobalConfig(threads_max=8)
    assert runtime.clamp_threads(3) == 3
    assert runtime.clamp_threads(64) == 8
    assert runtime.clamp_threads(0) == 1


def test_restart_is_only_a_due_check():
    off = GlobalConfig()
    assert off.restart_due(10 ** 6) is False
    on = GlobalConfig(restart_interval=60.0)
    assert on.restart_due(59.0) is False
    assert on.restart_due(60.0) is True


def test_overload_zero_means_off():
    assert GlobalConfig().overload_check(10_000) is True
    runtime = GlobalConfig(tp_overload=10)
    assert runtime.overload_check(9) is True
    assert runtime.overload_check(10) is False


def test_accounts_are_loaded_in_batches(tmp_path, monkeypatch):
    settings = load(tmp_path, "globals:\n  account_batch: 2\n")
    store = Store(settings.db_path)
    for index in range(5):
        label = f"acc{index}"
        settings.session_path(label).write_bytes(b"")
        store.add_account(Account(label, session=str(settings.session_path(label))))

    from ogtool.accounts import AccountPool

    pages: list[tuple[int, int]] = []
    original = store.list_accounts

    def spy(limit: int = 50, offset: int = 0):
        pages.append((limit, offset))
        return original(limit=limit, offset=offset)

    monkeypatch.setattr(store, "list_accounts", spy)
    pool = AccountPool(settings, store)
    rows = pool.list()
    # The whole pool comes back, but it is pulled two rows at a time.
    assert [item.label for item in rows] == [f"acc{index}" for index in range(5)]
    assert pages == [(2, 0), (2, 2), (2, 4)]
    store.close()


def test_the_settings_page_prints_the_six_knobs(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(BASE, encoding="utf-8")
    assert run(["--config", str(config), "settings"]) == 0
    output = capsys.readouterr().out
    for key in ("account_batch", "threads_max", "resume_delay", "tp_overload",
                "restart_interval", "restart_timed"):
        assert key in output
    assert "128" in output
    assert "restart due at: off" in output
    assert "overload check: off" in output


def test_the_settings_page_can_emit_json(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(BASE + "globals:\n  account_batch: 7\n",
                      encoding="utf-8")
    assert run(["--config", str(config), "settings", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["account_batch"] == 7
    assert payload["threads_max"] == 128
    assert set(payload) == {"account_batch", "threads_max", "resume_delay",
                            "tp_overload", "restart_interval", "restart_timed"}
