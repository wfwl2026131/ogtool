"""#7 watch chat: the exclusion list, the relay and the replay."""

from __future__ import annotations

import json

import pytest

from ogtool.config import ConfigError
from ogtool.features.watch_chat import (
    Incoming,
    WatchConfig,
    excluded,
    parse_incoming,
    run,
)
from ogtool.targets import parse_many


def test_excluded_returns_the_matching_keyword():
    assert excluded("Buy cheap PRICE now", ["spam", "price"]) == "price"
    assert excluded("hello", ["spam"]) == ""


def test_parse_incoming_accepts_pipe_rows_and_bare_text():
    rows = parse_incoming(["@peer|@sender|hello", "just text"])
    assert rows[0] == Incoming("@peer", "@sender", "hello")
    assert rows[1] == Incoming("", "", "just text")


def test_relay_and_skip_are_both_printed(ctx, capsys):
    config = WatchConfig(
        targets=parse_many(["@watch1"], "custom"),
        exclude=["spam"],
        relay="@relay",
        incoming=[Incoming("@watch1", "@a", "hello"),
                  Incoming("@watch1", "@b", "spam here")],
    )
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "relayed to @relay" in output
    assert "SKIP excluded by 'spam'" in output
    assert "relayed: 1  dropped: 1" in output


def test_no_relay_group_keeps_everything_out(ctx, capsys):
    config = WatchConfig(
        targets=parse_many(["@watch1"], "custom"),
        incoming=[Incoming("@watch1", "@a", "hello")],
    )
    run(config, ctx)
    assert "SKIP no relay group" in capsys.readouterr().out


def test_no_target_is_a_config_error():
    with pytest.raises(ConfigError):
        WatchConfig.from_dict({}, None)  # type: ignore[arg-type]


# -- the live path ----------------------------------------------------
#
# Everything below fails if ``--live`` only returns: the handler has to be
# registered on the client and the loop has to be entered, otherwise a watcher
# that watches nothing still exits 0, which is exactly the bug this covers.


class FakeLoop:
    """Stands in for the event loop: it records what was wired up first."""

    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.calls = 0
        self.handlers_seen: list[object] = []

    def run_forever(self) -> None:
        self.calls += 1
        self.handlers_seen = list(self.client.handlers)
        raise KeyboardInterrupt


class FakeMessage:
    id = 900


class FakeClient:
    def __init__(self) -> None:
        self.handlers: list[object] = []
        self.sent: list[tuple[object, str]] = []

    def add_event_handler(self, handler: object) -> None:
        self.handlers.append(handler)

    def remove_event_handler(self, handler: object) -> None:
        if handler in self.handlers:
            self.handlers.remove(handler)

    async def send_message(self, peer: object, text: str) -> object:
        self.sent.append((peer, text))
        return FakeMessage()


class FakeSender:
    def __init__(self, clients: dict[str, FakeClient]) -> None:
        self.clients = clients

    def _client(self, label: str) -> FakeClient:
        return self.clients[label]

    def close(self) -> None:
        pass


class FakeEvent:
    def __init__(self, client: FakeClient, chat_id: int, message_id: int,
                 text: str) -> None:
        self.client = client
        self.chat_id = chat_id
        self.id = message_id
        self.raw_text = text


def live_ctx(ctx, monkeypatch, clients: dict[str, FakeClient]) -> FakeLoop:
    """A live context with a fake transport and a loop that stops at once."""
    import asyncio

    pytest.importorskip("telethon")
    loop = FakeLoop(next(iter(clients.values())))
    ctx.live = True
    ctx._sender = FakeSender(clients)
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: loop)
    return loop


def test_live_registers_a_handler_and_enters_the_loop(ctx, monkeypatch):
    client = FakeClient()
    loop = live_ctx(ctx, monkeypatch, {"acc1": client, "acc2": client})
    config = WatchConfig(targets=parse_many(["@watch1"], "custom"),
                         relay="@relay", exclude=["spam"])

    assert run(config, ctx) == 0

    assert loop.calls == 1, "the event loop was never entered"
    assert len(loop.handlers_seen) == 2, "no NewMessage handler was registered"
    assert client.handlers == [], "the handler outlived the run"
    row = ctx.store.task_row(ctx.task_id)
    assert row is not None and row["status"] == "done"


def test_live_without_a_session_is_an_error(ctx, monkeypatch):
    ctx.live = True
    ctx._sender = FakeSender({})
    monkeypatch.setattr(
        "ogtool.runtime.RunContext.bind", lambda self, labels: [])
    config = WatchConfig(targets=parse_many(["@watch1"], "custom"),
                         relay="@relay")
    assert run(config, ctx) == 1
    row = ctx.store.task_row(ctx.task_id)
    assert row is not None and row["status"] == "failed"


def test_the_live_handler_drops_excluded_and_relays_the_rest(ctx, monkeypatch):
    import asyncio

    client = FakeClient()
    loop = live_ctx(ctx, monkeypatch, {"acc1": client, "acc2": client})
    config = WatchConfig(targets=parse_many(["@watch1"], "custom"),
                         relay="@relay", exclude=["spam"])
    run(config, ctx)
    handler = loop.handlers_seen[0]

    asyncio.run(handler(FakeEvent(client, -1001, 5, "hello there")))
    assert client.sent == [("@relay", "hello there")]

    asyncio.run(handler(FakeEvent(client, -1001, 6, "buy spam now")))
    assert len(client.sent) == 1, "an excluded message was relayed"

    # The same message delivered twice after a reconnect is not reposted.
    asyncio.run(handler(FakeEvent(client, -1001, 5, "hello there")))
    assert len(client.sent) == 1, "a duplicate was relayed twice"

    statuses = [row["status"] for row in ctx.store.results_for(ctx.task_id)]
    # The relay is a sent row; the two skips are ok rows too (the dry-run
    # path records them the same way), so the derived receipt counts all
    # three -- a skip nobody counts is how a live receipt said planned 0
    # with a row in the table.
    assert statuses.count("sent") == 3
    assert statuses.count("dropped") == 0


def test_numeric_targets_are_watched_as_ids_and_relay_as_written():
    from ogtool.features.watch_chat import relay_peers, watch_peers

    config = WatchConfig(targets=parse_many(["6805350975"], "custom"),
                         relay="+15550000001")
    assert watch_peers(config) == [6805350975]
    assert relay_peers(config.relay) == ["+15550000001"]


def test_include_keywords_drop_messages_that_do_not_carry_one(ctx, capsys):
    """``includeKeywords`` is the page's other box, next to the exclude list."""
    config = WatchConfig(targets=parse_many(["@g1"], "custom"),
                         relay="@relay", include=["bitcoin"],
                         incoming=[Incoming("@g1", "Ann", "bitcoin is up"),
                                   Incoming("@g1", "Bob", "weather today")])
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "relayed to @relay" in output
    assert "SKIP no include keyword" in output


def test_watch_all_ignores_the_include_box(ctx, capsys):
    config = WatchConfig(targets=parse_many(["@g1"], "custom"),
                         relay="@relay", include=["bitcoin"],
                         watch_mode="all",
                         incoming=[Incoming("@g1", "Bob", "weather today")])
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "SKIP no include keyword" not in output
    assert "relayed to @relay" in output
    assert "watch all" in output


def test_a_skipped_message_is_not_a_failure(ctx, capsys):
    """dropped counted as failed, so the receipt said "failed 1" with no row."""
    from ogtool import receipts

    config = WatchConfig(targets=parse_many(["@peer"], "custom"),
                         incoming=[Incoming("@peer", "@sender", "hello")])
    assert run(config, ctx) == 0
    capsys.readouterr()
    body = json.loads(next(receipts.receipt_dir(ctx.settings)
                           .glob("watch_chat-*.json")).read_text())
    assert body["failed"] == 0
    assert body["ok"] == body["results"]["total"] == 1
    assert receipts.validate(body) == []


def test_run_seconds_stops_the_watch_loop_without_a_signal(monkeypatch, ctx,
                                                           capsys, settings):
    """watch_chat --run-seconds N: the watch loop schedules its own stop and
    the receipt closes in the same finally as an interrupt."""
    import asyncio

    from ogtool.features.watch_chat import WatchConfig, run

    class StopLoop:
        def __init__(self):
            self.scheduled = False

        def call_later(self, delay, callback):
            assert delay == 3
            self.scheduled = True

        def call_soon_threadsafe(self, callback):
            pass

        def run_forever(self):
            assert self.scheduled, "the self-stop was not scheduled"

    monkeypatch.setattr(asyncio, "get_event_loop", lambda: StopLoop())
    # A live context needs an openable client per bound account: the fake
    # sender hands one out, the same stand-in the page's own live tests use.
    ctx._sender = FakeSender({"acc1": FakeClient(), "acc2": FakeClient()})
    ctx.live = True
    config = WatchConfig.from_dict(
        {"watch_chat": {"targets": "@g1", "run_seconds": 3}}, settings)
    code = run(config, ctx)
    assert code == 0
    out = capsys.readouterr().out
    assert "stopping after 3s" in out
    assert "DONE:" in out
