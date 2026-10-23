"""#15 proxy: parsing, the table operations, the latency probe and binding."""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features.proxy import ProxyConfig, parse_proxy, run


def test_parse_proxy_accepts_a_full_url():
    proxy = parse_proxy("socks5://user:pass@1.2.3.4:1080")
    assert (proxy.kind, proxy.host, proxy.port) == ("socks5", "1.2.3.4", 1080)
    assert proxy.user == "user" and proxy.password == "pass"


def test_parse_proxy_accepts_a_bare_host_port():
    proxy = parse_proxy("5.6.7.8:8080")
    assert (proxy.kind, proxy.host, proxy.port) == ("socks5", "5.6.7.8", 8080)


def test_parse_proxy_rejects_an_unknown_kind():
    with pytest.raises(ConfigError):
        parse_proxy("ftp://host:21")


def test_add_and_list(ctx, capsys):
    config = ProxyConfig(add=["socks5://1.1.1.1:1080", "http://2.2.2.2:8080"])
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "总计: 2" in output
    assert "socks5://1.1.1.1:1080" in output


def test_duplicate_proxies_are_collapsed(ctx, capsys):
    assert run(ProxyConfig(add=["1.1.1.1:1080"]), ctx) == 0
    capsys.readouterr()
    assert run(ProxyConfig(add=["1.1.1.1:1080"]), ctx) == 0
    assert "总计: 1" in capsys.readouterr().out


def test_test_marks_the_slow_ones_as_timeout(ctx, capsys):
    run(ProxyConfig(add=["1.1.1.1:1080"]), ctx)
    capsys.readouterr()
    assert run(ProxyConfig(test=True, timeout_ms=30), ctx) == 0
    output = capsys.readouterr().out
    assert "测速 #" in output
    assert "超时：" in output


def test_clear_removes_every_row(ctx, capsys):
    run(ProxyConfig(add=["1.1.1.1:1080"]), ctx)
    capsys.readouterr()
    assert run(ProxyConfig(clear=True), ctx) == 0
    assert "清空：1 proxies removed" in capsys.readouterr().out


def test_delete_reports_a_missing_id(ctx, capsys):
    assert run(ProxyConfig(delete=99), ctx) == 1
    assert "not found" in capsys.readouterr().out


def test_bind_writes_the_account_column(ctx, capsys):
    run(ProxyConfig(add=["1.1.1.1:1080"]), ctx)
    capsys.readouterr()
    assert run(ProxyConfig(bind=["acc1=1"]), ctx) == 0
    assert "bind acc1 -> #1" in capsys.readouterr().out
    assert ctx.store.get_account("acc1").proxy == "socks5://1.1.1.1:1080"


class RefusingSender:
    """A transport that cannot apply this proxy, as with an mtproxy URL."""

    def set_proxy(self, label: str, url: str):
        from ogtool.senders import SendResult

        return SendResult(ok=False, code="UNKNOWN",
                          detail="unsupported proxy kind: mtproxy")

    def close(self) -> None:
        pass


def test_a_failed_bind_is_not_reported_as_bound(ctx, live, capsys):
    live(RefusingSender())
    run(ProxyConfig(add=["socks5://1.2.3.4:1080"], bind=["acc1=1"]), ctx)
    output = capsys.readouterr().out
    assert "bind acc1 -> #1 UNKNOWN" in output
    assert "bind acc1 -> #1 socks5://1.2.3.4:1080" not in output
