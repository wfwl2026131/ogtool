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


def test_testing_proxies_leaves_one_row_per_proxy(tmp_path):
    """The speed test is the page's real action, so it must be backable by rows."""
    import json

    from ogtool.config import Settings
    from ogtool.features import proxy as page
    from ogtool.runtime import RunContext
    from ogtool.store import Store

    settings = Settings(tmp_path / "config.yaml", log_dir=tmp_path / "logs")
    store = Store(tmp_path / "og.sqlite")
    ctx = RunContext(settings, store, __import__("logging").getLogger("t"))
    ctx.store.add_proxy(page.parse_proxy("socks5://127.0.0.1:1"))
    ctx.store.add_proxy(page.parse_proxy("socks5://127.0.0.1:2"))
    config = page.ProxyConfig(test=True, timeout_ms=50)
    page.run(config, ctx)

    receipts = sorted((tmp_path / "logs" / "receipts").glob("proxy-*.json"))
    assert receipts, "no receipt was written"
    body = json.loads(receipts[-1].read_text())
    # A receipt stores counts, not row bodies: the counts are what must match.
    assert body["planned"] == 2
    assert body["results"]["total"] == 2
    assert body["results"]["failed"] == 2
    assert body["failed"] == 2
    assert body["unattributed"] == 2  # "-": probes belong to no account


def test_session_key_template_expands_per_account(ctx, capsys):
    """A {SessionKey} row binds each account its own sticky key, row keeps raw."""
    from ogtool.features.proxy import parse_proxy

    row = parse_proxy(
        "socks5://user:pass_session-{SessionKey}_lifetime-10m@geo.example:43324")
    assert row.password == "pass_session-{SessionKey}_lifetime-10m"
    run(ProxyConfig(add=[row.url()]), ctx)
    capsys.readouterr()
    assert run(ProxyConfig(bind=["acc1=1", "acc2=1"]), ctx) == 0
    acc1 = ctx.store.get_account("acc1").proxy
    acc2 = ctx.store.get_account("acc2").proxy
    assert acc1 != acc2, "each account must get its own session key"
    assert "{SessionKey}" not in acc1 and "{SessionKey}" not in acc2
    assert "_lifetime-10m@" in acc1 and "_lifetime-10m@" in acc2
    # The table row itself keeps the raw template.
    raw = ctx.store.list_proxies()[0].url()
    assert "{SessionKey}" in raw


def test_session_key_grammar_matches_the_reference_help():
    """{SessionKey}=8 alnum, {SessionKey#12}=12 alnum, {SessionKey#d#12}=12 digits."""
    import random

    from ogtool.features.proxy import resolve_session_keys

    rng = random.Random(7)
    short = resolve_session_keys("socks5://h:1@u:{SessionKey}@g:2", rng)
    long = resolve_session_keys("socks5://h:1@u:{SessionKey#12}@g:2", rng)
    digits = resolve_session_keys("socks5://h:1@u:{SessionKey#d#12}@g:2", rng)
    key = short.split("u:")[1].split("@")[0]
    assert len(key) == 8 and key.isalnum()
    assert len(long.split("u:")[1].split("@")[0]) == 12
    assert len(digits.split("u:")[1].split("@")[0]) == 12
    assert digits.split("u:")[1].split("@")[0].isdigit()


def test_rotate_moves_accounts_off_a_timed_out_proxy(ctx, capsys):
    """The rotation manager's job: re-test, then rebind onto the fastest ok."""
    run(ProxyConfig(add=["socks5://10.0.0.1:1080", "socks5://10.0.0.2:1080"]), ctx)
    capsys.readouterr()
    # First probe: 10.0.0.1 dies (port 1 is closed), 10.0.0.2 lives (port 9).
    ctx.store.set_proxy_latency(1, 3000, "timeout")
    ctx.store.set_proxy_latency(2, 12, "ok")
    run(ProxyConfig(bind=["acc1=1"]), ctx)
    capsys.readouterr()
    assert "10.0.0.1" in ctx.store.get_account("acc1").proxy

    assert run(ProxyConfig(rotate=True), ctx) == 0
    output = capsys.readouterr().out
    moved = ctx.store.get_account("acc1").proxy
    assert "10.0.0.2" in moved, f"acc1 should move to the ok proxy, got {moved}"
    assert "ROTATE: 1 account(s) moved" in output


def test_rotate_with_nothing_down_leaves_bindings(ctx, capsys):
    run(ProxyConfig(add=["socks5://10.0.0.2:9"]), ctx)
    capsys.readouterr()
    ctx.store.set_proxy_latency(1, 10, "ok")
    run(ProxyConfig(bind=["acc1=1"]), ctx)
    capsys.readouterr()
    assert run(ProxyConfig(rotate=True), ctx) == 0
    assert "nothing timed out" in capsys.readouterr().out
    assert "10.0.0.2" in ctx.store.get_account("acc1").proxy
