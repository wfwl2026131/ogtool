"""#9 reads ``batch_login_config.json`` by the keys the binary stores.

``qrTimeoutSec``, ``smsTimeoutSec``, ``loginBaseApi``, ``twoFactorPassword``,
``dynamicProxyEnabled`` / ``dynamicProxy``, ``hideImportDialog``,
``importSessionOnDrop``, ``collectNestedSessionFiles``, ``loginMethod``,
``loginMode``, ``maxConcurrent``.

The two refusals the page itself makes are refusals here too: a base API that is
not http(s), and a dynamic proxy that is enabled but empty.
"""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features.batch_login import LoginConfig, proxy_for, run


def _settings():
    return type("S", (), {"runtime": type("R", (), {
        "clamp_threads": staticmethod(lambda value: value)})()})()


def test_the_page_keys_are_read(tmp_path):
    config = LoginConfig.from_dict(
        {"batch_login": {
            "import_folder": str(tmp_path),
            "qr_timeout_sec": 90,
            "sms_timeout_sec": 45,
            "login_base_api": "https://panel.example.com",
            "login_method": "qr",
            "login_mode": "ui",
            "max_concurrent": 4,
            "hide_import_dialog": True,
        }},
        _settings(),
    )
    assert (config.qr_timeout_sec, config.sms_timeout_sec) == (90, 45)
    assert config.login_base_api == "https://panel.example.com"
    assert (config.login_method, config.login_mode) == ("qr", "ui")
    assert config.max_concurrent == 4
    assert config.hide_import_dialog is True


def test_a_base_api_without_a_scheme_is_refused(tmp_path):
    with pytest.raises(ConfigError) as caught:
        LoginConfig.from_dict(
            {"batch_login": {"import_folder": str(tmp_path),
                             "login_base_api": "panel.example.com"}},
            _settings(),
        )
    assert "http" in str(caught.value)


def test_a_dynamic_proxy_that_is_enabled_but_empty_is_refused(tmp_path):
    with pytest.raises(ConfigError) as caught:
        LoginConfig.from_dict(
            {"batch_login": {"import_folder": str(tmp_path),
                             "dynamic_proxy_enabled": True}},
            _settings(),
        )
    assert "empty" in str(caught.value)


def test_a_malformed_proxy_is_refused(tmp_path):
    with pytest.raises(ConfigError):
        LoginConfig.from_dict(
            {"batch_login": {"import_folder": str(tmp_path),
                             "dynamic_proxy_enabled": True,
                             "dynamic_proxy": "host-without-port"}},
            _settings(),
        )


def test_a_bad_port_is_refused(tmp_path):
    with pytest.raises(ConfigError):
        LoginConfig.from_dict(
            {"batch_login": {"import_folder": str(tmp_path),
                             "dynamic_proxy_enabled": True,
                             "dynamic_proxy": "1.2.3.4:99999:u:p"}},
            _settings(),
        )


def test_a_good_proxy_is_accepted(tmp_path):
    config = LoginConfig.from_dict(
        {"batch_login": {"import_folder": str(tmp_path),
                         "dynamic_proxy_enabled": True,
                         "dynamic_proxy": "geo.example.com:43324:user:pw_"
                                          "{SessionKey}_lifetime-10m"}},
        _settings(),
    )
    assert config.dynamic_proxy_enabled is True
    assert "{SessionKey}" in config.dynamic_proxy


def test_the_session_key_is_filled_per_account():
    config = LoginConfig(dynamic_proxy_enabled=True,
                         dynamic_proxy="h:1:u:{SessionKey}")
    one = proxy_for(config, "acc1")
    two = proxy_for(config, "acc2")
    assert "{SessionKey}" not in one
    assert one != two
    assert one.split(":")[3].isalnum()


def test_the_sized_session_keys_are_filled_with_the_right_length():
    config = LoginConfig(dynamic_proxy_enabled=True,
                         dynamic_proxy="h:1:u:{SessionKey#12}")
    assert len(proxy_for(config, "acc1").split(":")[3]) == 12
    digits = LoginConfig(dynamic_proxy_enabled=True,
                         dynamic_proxy="h:1:u:{SessionKey#d#12}")
    key = proxy_for(digits, "acc1").split(":")[3]
    assert len(key) == 12 and key.isdigit()


def test_no_proxy_means_no_line():
    assert proxy_for(LoginConfig(), "acc1") == ""


def test_the_run_never_prints_the_two_factor_password(ctx, tmp_path, capsys):
    config = LoginConfig(import_folder=str(tmp_path),
                         two_factor_password="hunter2")
    run(config, ctx)
    output = capsys.readouterr().out
    assert "hunter2" not in output
    assert "two-factor password: set" in output


def test_the_run_never_prints_the_proxy_password(ctx, tmp_path, capsys):
    config = LoginConfig(import_folder=str(tmp_path),
                         dynamic_proxy_enabled=True,
                         dynamic_proxy="1.2.3.4:8080:user:s3cret")
    run(config, ctx)
    output = capsys.readouterr().out
    assert "s3cret" not in output
    assert "dynamic proxy: 1.2.3.4:8080" in output
