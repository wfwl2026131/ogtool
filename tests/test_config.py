"""Config loading: environment references, merging and defaults."""

from __future__ import annotations

import pytest

from ogtool.config import AIConfig, ConfigError, Settings, _expand, merge


def test_expand_uses_environment(monkeypatch):
    monkeypatch.setenv("OGTEST_VALUE", "hello")
    assert _expand("${OGTEST_VALUE}") == "hello"
    assert _expand({"a": ["${OGTEST_VALUE}"]}) == {"a": ["hello"]}


def test_expand_default_when_missing():
    assert _expand("${OGTEST_MISSING:fallback}") == "fallback"


def test_expand_raises_without_default():
    with pytest.raises(ConfigError):
        _expand("${OGTEST_MISSING}")


def test_merge_is_deep_and_does_not_mutate():
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    override = {"a": {"c": 9}}
    merged = merge(base, override)
    assert merged == {"a": {"b": 1, "c": 9}, "d": 3}
    assert base == {"a": {"b": 1, "c": 2}, "d": 3}


def test_settings_loads_relative_paths(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "api_id: 123\napi_hash: abc\nsession_dir: state/s\n"
        "db: state/db.sqlite\nlogs: state/logs\n",
        encoding="utf-8",
    )
    settings = Settings.load(config)
    assert settings.api_id == 123
    assert settings.session_dir == tmp_path / "state" / "s"
    assert settings.db_path.parent.is_dir()
    assert settings.require_credentials() == (123, "abc")


def test_settings_requires_credentials_for_live(tmp_path, monkeypatch):
    # The fallback reads the environment, so the test must not inherit a secret
    # that happens to be exported on the machine running it.
    for name in ("DSH_SECRET_TG_API_CREDENTIALS", "TG_API_CREDENTIALS",
                 "TG_API_ID", "TG_API_HASH", "OGTOOL_API_ID", "OGTOOL_API_HASH"):
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / "config.yaml"
    config.write_text("api_id: 0\napi_hash: ''\n", encoding="utf-8")
    settings = Settings.load(config)
    with pytest.raises(ConfigError):
        settings.require_credentials()


def test_ai_config_disabled_without_key():
    assert not AIConfig().enabled
    assert AIConfig(base_url="http://x", api_key="k", model="m").enabled
