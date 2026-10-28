"""#15 proxy - the proxy table: add, import, paste, clear, test, bind, rotate.

The page is the only table view in the console: a list of proxies with a status
column, plus select all / import / paste / clear / delete / speed test.  Rows live in the
``proxies`` table; binding one to an account writes the account's ``proxy``
column, which is what the sender reads when it opens a client.

Two behaviours mirror the reference client's dynamic-proxy page:

- a row whose URL carries ``{SessionKey}`` (optionally ``{SessionKey#12}`` for
  a longer key, ``{SessionKey#d#12}`` for digits only) is a rotating-residential
  template: binding expands one fresh random key **per account**, so every
  account pins its own exit IP for the session lifetime - the row itself keeps
  the raw template;
- ``--rotate`` rebinds any account whose bound proxy tested as ``timeout`` to
  the fastest ``ok`` row (the reference's ``ProxyRotationManager`` doing its
  ``startNextCheck``).
"""

from __future__ import annotations

import random
import re
import string
from dataclasses import dataclass, field

from ..config import ConfigError, Settings
from ..flags import Flag
from ..report import read_lines
from ..runtime import RunContext
from ..store import Proxy
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    name_of,
    num,
    open_task,
    record,
    section,
    text,
)

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


SESSION_KEY = re.compile(r"\{SessionKey(?:#d)?(?:#(\d+))?\}")


def resolve_session_keys(url: str, rng: random.Random) -> str:
    """Expand every ``{SessionKey}`` placeholder into one fresh sticky key.

    ``{SessionKey}`` -> 8 alphanumerics, ``{SessionKey#12}`` -> 12
    alphanumerics, ``{SessionKey#d#12}`` -> 12 digits - the exact grammar the
    reference client's dynamic-proxy help text spells out.  One expansion per
    placeholder: every account bound from the same template row gets its own
    key, which is what pins a different exit IP to each account.
    """
    def _one(match: re.Match[str]) -> str:
        length = int(match.group(1) or 8)
        alphabet = (string.digits if "#d" in match.group(0)
                    else string.ascii_letters + string.digits)
        return "".join(rng.choice(alphabet) for _ in range(length))

    return SESSION_KEY.sub(_one, url)


def _sticky(proxy: Proxy, rng: random.Random) -> Proxy:
    """A concrete copy of ``proxy`` with any session-key template expanded."""
    url = proxy.url()
    if "{SessionKey" not in url:
        return proxy
    resolved = parse_proxy(resolve_session_keys(url, rng))
    resolved.latency_ms = proxy.latency_ms
    resolved.status = proxy.status
    return resolved


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
    Flag("--rotate", "rotate", None,
         "move accounts off a proxy whose last test timed out to the fastest "
         "ok one (combine with --test for a fresh check)",
         action="store_true"),
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
    rotate: bool = False
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
            rotate=bool(data.get("rotate", False)),
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
    task = None
    if config.test and rows:
        # Testing is the page's only action that talks to the outside world, so
        # it is the only part that leaves a receipt: one row per proxy, because
        # 'ok: 3' with no rows is exactly the shape this project refuses.
        task = open_task(ctx, config.task_name, FEATURE,
                         {"add": config.add, "delete": config.delete,
                          "timeout": config.timeout_ms,
                          "test": config.test, "rotate": config.rotate},
                         planned=len(rows))
        _test(rows, config.timeout_ms, ctx, task)
        rows = ctx.store.list_proxies()

    if config.bind:
        _bind(rows, config, ctx)

    if config.rotate:
        _rotate(rows, ctx)

    rows = ctx.store.list_proxies()
    print(f"总计: {len(rows)}")
    for proxy in rows:
        latency = "-" if proxy.latency_ms < 0 else f"{proxy.latency_ms} ms"
        print(f"#{proxy.id}\t{proxy.display()}\t{proxy.status}\t{latency}")
    timeouts = [row for row in rows if row.status == "timeout"]
    print(f"超时：{len(timeouts)}")
    print(f"PLAN: {added} added, {len(rows)} proxies, {len(timeouts)} timeout")
    if task is not None:
        close_task(ctx, task, derive=True)
    return 0


def _test(rows: list[Proxy], timeout_ms: int, ctx: RunContext,
          task: object | None = None) -> None:
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
        if task is not None:
            # "-" in the account column: a proxy probe belongs to no account,
            # and putting the proxy URL there would make the receipt claim an
            # account named after a URL.
            record(ctx, task, "-", f"#{proxy.id} {proxy.display()}",
                   status == "ok", "" if status == "ok" else "TIMEOUT",
                   f"{latency} ms")
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
    rng = random.Random()
    for spec in config.bind:
        label, _, target_id = spec.partition("=")
        label = label.strip()
        proxy = by_id.get(int(target_id)) if target_id.strip().isdigit() else None
        if proxy is None:
            print(f"bind {label}: proxy not found: {target_id!r}")
            continue
        # A {SessionKey} template row expands per account: each account pins
        # its own sticky key (its own exit IP) in the accounts.proxy column,
        # while the table row keeps the raw template for the next account.
        concrete = _sticky(proxy, rng)
        ctx.store.bind_proxy(label, concrete)
        stored = getattr(ctx.store.get_account(label), "proxy", "") or concrete.url()
        answer = ctx.sender().set_proxy(label, stored)  # the real URL
        if answer.ok:
            print(f"bind {label} -> #{proxy.id} {concrete.display()}")
        else:
            print(f"bind {label} -> #{proxy.id} {answer.code} {answer.detail}")


def _rotate(rows: list[Proxy], ctx: RunContext) -> None:
    """Move accounts off a proxy that just timed out (startNextCheck's job).

    Matching is by endpoint, not by URL: a bound {SessionKey} URL never equals
    its template row's URL, but the host:port pair is what identifies the
    proxy behind it.
    """
    accounts = ctx.bind([])
    if not accounts:
        print("rotate: no account bound (dry-run)")
        return
    healthy = sorted((row for row in rows if row.status == "ok"),
                      key=lambda row: row.latency_ms)
    dead = {(row.host, row.port) for row in rows if row.status == "timeout"}
    if not dead:
        print("ROTATE: nothing timed out, bindings stay")
        return
    if not healthy:
        print("ROTATE: no healthy proxy, bindings stay")
        return
    rng = random.Random()
    moved = 0
    for account in accounts:
        bound = (getattr(ctx.store.get_account(account.label), "proxy", "")
                 or "").strip()
        if _endpoint_of(bound) not in dead:
            continue
        target = healthy[0]
        if _endpoint_of(bound) == (target.host, target.port):
            target = healthy[1] if len(healthy) > 1 else None
        if target is None:
            continue
        concrete = _sticky(target, rng)
        ctx.store.bind_proxy(account.label, concrete)
        stored = ctx.store.get_account(account.label).proxy
        answer = ctx.sender().set_proxy(account.label, stored)
        if answer.ok:
            moved += 1
            print(f"ROTATE: {account.label} -> #{target.id} "
                  f"{concrete.display()} ({target.latency_ms} ms)")
        else:
            print(f"ROTATE: {account.label} -> #{target.id} "
                  f"{answer.code} {answer.detail}")
    print(f"ROTATE: {moved} account(s) moved")


def _endpoint_of(url: str) -> tuple[str, int]:
    """``socks5://user:pass@host:port`` -> ``(host, port)``."""
    from urllib.parse import urlsplit
    parts = urlsplit(url or "")
    return (parts.hostname or "", parts.port or 0)


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(ProxyConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["KINDS", "ProxyConfig", "TITLE", "main", "parse_proxy", "run"]
