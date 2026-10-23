"""#15 proxy - the proxy table: add, import, paste, clear, test, bind.

The page is the only table view in the console: a list of proxies with a status
column, plus select all / import / paste / clear / delete / speed test.  Rows live in the
``proxies`` table; binding one to an account writes the account's ``proxy``
column, which is what the sender reads when it opens a client.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..config import ConfigError, Settings
from ..flags import Flag
from ..report import read_lines
from ..runtime import RunContext
from ..store import Proxy
from . import register
from ._common import accounts_of, header, name_of, num, section, text

FEATURE = "proxy"
SECTION = "proxy"
TITLE = "#15 代理配置"

KINDS = ("socks5", "http", "mtproxy")


def parse_proxy(value: str) -> Proxy:
    """``socks5://user:pass@host:port`` or ``host:port`` -> a row."""
    text_value = value.strip()
    if not text_value:
        raise ConfigError("empty proxy")
    kind = "socks5"
    if "://" in text_value:
        kind, text_value = text_value.split("://", 1)
    kind = kind.lower()
    if kind not in KINDS:
        raise ConfigError(f"unknown proxy kind: {kind}")
    user = password = ""
    if "@" in text_value:
        credentials, text_value = text_value.rsplit("@", 1)
        if ":" in credentials:
            user, password = credentials.split(":", 1)
        else:
            user = credentials
    host, _, port_text = text_value.partition(":")
    if not host:
        raise ConfigError(f"no host in {value!r}")
    try:
        port = int(port_text) if port_text else 0
    except ValueError as error:
        raise ConfigError(f"bad port in {value!r}") from error
    return Proxy(kind=kind, host=host, port=port, user=user, password=password)


FLAGS = (
    Flag("--add", "add", str, "add one proxy (socks5://host:port)", action="append"),
    Flag("--import", "import_file", str, "file with one proxy per line"),
    Flag("--paste", "paste", str, "paste a blob of proxies, one per line"),
    Flag("--delete", "delete", int, "delete the proxy with this id"),
    Flag("--clear", "clear", None, "delete every proxy", action="store_true"),
    Flag("--test", "test", None, "measure latency for every proxy",
         action="store_true"),
    Flag("--timeout-ms", "timeout_ms", int, "a proxy slower than this is a timeout"),
    Flag("--bind", "bind", str, "account=proxy_id (repeatable)", action="append"),
)


@dataclass(slots=True)
class ProxyConfig:
    account_labels: list[str] = field(default_factory=list)
    add: list[str] = field(default_factory=list)
    import_file: str = ""
    paste: str = ""
    delete: int = 0
    clear: bool = False
    test: bool = False
    timeout_ms: int = 3000
    bind: list[str] = field(default_factory=list)
    task_name: str = "proxy"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> ProxyConfig:
        del settings
        data = section(raw, SECTION)
        add = data.get("add") or []
        if isinstance(add, str):
            add = [add]
        bind = data.get("bind") or []
        if isinstance(bind, str):
            bind = [bind]
        return cls(
            account_labels=accounts_of(raw, data),
            add=[str(item) for item in add],
            import_file=text(data, "import_file"),
            paste=text(data, "paste"),
            delete=num(data, "delete", 0, kind=int),
            clear=bool(data.get("clear", False)),
            test=bool(data.get("test", False)),
            timeout_ms=num(data, "timeout_ms", 3000, kind=int),
            bind=[str(item) for item in bind],
            task_name=name_of(raw, data, "proxy"),
        )


def run(config: ProxyConfig, ctx: RunContext) -> int:
    header(config.task_name, timeout=f"{config.timeout_ms} ms",
           test="on" if config.test else "off")

    if config.clear:
        removed = ctx.store.clear_proxies()
        print(f"清空：{removed} proxies removed")
        return 0

    if config.delete:
        removed = ctx.store.delete_proxy(config.delete)
        print(f"删除 #{config.delete}：{'ok' if removed else 'not found'}")
        return 0 if removed else 1

    added = 0
    entries = list(config.add)
    if config.import_file:
        entries.extend(read_lines(config.import_file))
    if config.paste:
        entries.extend(line for line in config.paste.replace(",", "\n").splitlines()
                       if line.strip())
    for entry in entries:
        proxy = ctx.store.add_proxy(parse_proxy(entry))
        added += 1
        print(f"added #{proxy.id} {proxy.display()}")

    rows = ctx.store.list_proxies()
    if config.test:
        _test(rows, config.timeout_ms, ctx)
        rows = ctx.store.list_proxies()

    if config.bind:
        _bind(rows, config, ctx)

    rows = ctx.store.list_proxies()
    print(f"总计: {len(rows)}")
    for proxy in rows:
        latency = "-" if proxy.latency_ms < 0 else f"{proxy.latency_ms} ms"
        print(f"#{proxy.id}\t{proxy.display()}\t{proxy.status}\t{latency}")
    timeouts = [row for row in rows if row.status == "timeout"]
    print(f"超时：{len(timeouts)}")
    print(f"PLAN: {added} added, {len(rows)} proxies, {len(timeouts)} timeout")
    return 0


def _test(rows: list[Proxy], timeout_ms: int, ctx: RunContext) -> None:
    """Latency probe: a real TCP connect when live, a fixed spread in a dry run."""
    rng = random.Random(len(rows))
    for proxy in rows:
        if ctx.live:
            measured = _probe(proxy, timeout_ms)
            latency, status = (measured, "ok") if measured >= 0 \
                else (timeout_ms, "timeout")
        else:
            latency = rng.randint(20, timeout_ms + timeout_ms // 2)
            status = "timeout" if latency > timeout_ms else "ok"
        ctx.store.set_proxy_latency(proxy.id, latency, status)
        print(f"测速 #{proxy.id} {proxy.display()} -> {latency} ms ({status})")


def _probe(proxy: Proxy, timeout_ms: int) -> int:
    """Milliseconds to open a TCP connection, or -1 when it does not open."""
    import socket
    import time

    started = time.monotonic()
    try:
        with socket.create_connection((proxy.host, proxy.port),
                                      timeout=timeout_ms / 1000.0):
            return int((time.monotonic() - started) * 1000)
    except OSError:
        return -1


def _bind(rows: list[Proxy], config: ProxyConfig, ctx: RunContext) -> None:
    accounts = ctx.bind(config.account_labels)
    if not accounts:
        print("bind: no account bound (dry-run)")
        return
    by_id = {row.id: row for row in rows}
    for spec in config.bind:
        label, _, target_id = spec.partition("=")
        label = label.strip()
        proxy = by_id.get(int(target_id)) if target_id.strip().isdigit() else None
        if proxy is None:
            print(f"bind {label}: proxy not found: {target_id!r}")
            continue
        ctx.store.bind_proxy(label, proxy)
        ctx.sender().set_proxy(label, proxy.url())  # the real URL
        print(f"bind {label} -> #{proxy.id} {proxy.display()}")


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(ProxyConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["KINDS", "ProxyConfig", "TITLE", "main", "parse_proxy", "run"]
