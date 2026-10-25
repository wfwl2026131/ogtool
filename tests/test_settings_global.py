"""#24 reads the global keys the binary keeps in ``deep_config.json``.

The page has six numbers of its own; the file it writes carries the whole
client's settings. This reports the keys this page understands, counts the ones
it does not, and never prints a token — a secret that shows up in a log is a
secret that has to be rotated.
"""

from __future__ import annotations

import json

import pytest

from ogtool.config import ConfigError
from ogtool.features.settings import (
    GLOBAL_KEYS,
    SettingsPageConfig,
    read_global,
    run,
)


def _write(tmp_path, payload) -> str:
    path = tmp_path / "deep_config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_the_known_keys_are_the_binaries_names():
    keys = {key for key, _label, _kind in GLOBAL_KEYS}
    for expected in ("accountBatchLoadSize", "autoRestartEnabled",
                     "batchLoginCodeModeEnabled", "apiIdIos",
                     "devicePlatformAndroidEnabled", "deepLogMode"):
        assert expected in keys


def test_values_come_back_for_the_known_keys(tmp_path):
    path = _write(tmp_path, {"accountBatchLoadSize": 25,
                             "autoRestartEnabled": True,
                             "deepLogMode": "console"})
    values, unknown, secrets_set = read_global(path)
    assert values == {"accountBatchLoadSize": 25,
                      "autoRestartEnabled": True,
                      "deepLogMode": "console"}
    assert (unknown, secrets_set) == (0, 0)


def test_unknown_keys_are_counted_not_rejected(tmp_path):
    path = _write(tmp_path, {"accountBatchLoadSize": 1, "otherPage": None,
                             "anotherPage": 2})
    _values, unknown, _secrets = read_global(path)
    assert unknown == 2


def test_a_token_is_never_printed(tmp_path):
    path = _write(tmp_path, {"freeMeBotToken": "123456:abcdef",
                             "apiHashIos": "deadbeef"})
    values, _unknown, secrets_set = read_global(path)
    assert values["freeMeBotToken"] == "<set>"
    assert values["apiHashIos"] == "<set>"
    assert secrets_set == 2
    assert "abcdef" not in json.dumps(values)


def test_an_empty_token_says_empty_rather_than_showing_nothing(tmp_path):
    path = _write(tmp_path, {"freeMeBotToken": ""})
    values, _unknown, _secrets = read_global(path)
    assert values["freeMeBotToken"] == "<empty>"


def test_a_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        read_global(str(tmp_path / "nope.json"))


def test_broken_json_is_a_config_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        read_global(str(path))


def test_a_json_array_is_a_config_error(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ConfigError):
        read_global(str(path))


def test_the_page_prints_the_global_block(ctx, tmp_path, capsys):
    path = _write(tmp_path, {"accountBatchLoadSize": 25,
                             "freeMeBotToken": "123:token",
                             "otherPage": 1})
    config = SettingsPageConfig(global_file=path)
    run(config, ctx)
    output = capsys.readouterr().out
    assert "global config:" in output
    assert "accountBatchLoadSize" in output
    assert "<set>" in output
    assert "123:token" not in output
    assert "global keys read: 2" in output
    assert "unknown in this page: 1" in output


def test_json_mode_carries_the_global_block(ctx, tmp_path, capsys):
    path = _write(tmp_path, {"accountBatchLoadSize": 25})
    run(SettingsPageConfig(as_json=True, global_file=path), ctx)
    payload = json.loads(capsys.readouterr().out)
    assert payload["global"] == {"accountBatchLoadSize": 25}
    assert "account_batch" in payload
