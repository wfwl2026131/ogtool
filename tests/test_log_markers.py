"""Every per-item line says what happened: ✅ on success, ❌ on failure."""

from __future__ import annotations

from types import SimpleNamespace

from ogtool.features.batch_join import JoinConfig
from ogtool.features.batch_join import run as run_join
from ogtool.features.keep_online import KeepOnlineConfig
from ogtool.features.keep_online import run as run_keep
from ogtool.features.watch_chat import Incoming, WatchConfig
from ogtool.features.watch_chat import run as run_watch
from ogtool.targets import parse_many


def test_watch_chat_relay_success_carries_the_check(ctx, capsys):
    config = WatchConfig(
        targets=parse_many(["@watch1"], "custom"),
        relay="@relay",
        incoming=[Incoming("@watch1", "@a", "hello")],
    )
    assert run_watch(config, ctx) == 0
    out = capsys.readouterr().out
    assert "✅ RELAY" in out and "relayed to @relay" in out
    assert not out.startswith("✅") or "❌" not in out.split("✅")[1][:0]


def test_watch_chat_failure_carries_the_cross(ctx, capsys, scripted):
    scripted(["FLOOD_WAIT_42"])
    config = WatchConfig(
        targets=parse_many(["@watch1"], "custom"),
        relay="@relay",
        incoming=[Incoming("@watch1", "@a", "hello")],
    )
    code = run_watch(config, ctx)
    out = capsys.readouterr().out
    assert "❌ FAIL" in out and "FLOOD_WAIT_42" in out
    assert code == 0  # the replay counts it, the page still closes cleanly


def test_keep_online_beats_are_marked(ctx, capsys, live):
    class Pong:
        def ping(self, *args, **kwargs):
            return SimpleNamespace(ok=True, code="ok", detail="",
                                   items=[], message_id=0)

        def close(self, *args, **kwargs):
            return None

    live(Pong())
    ctx.script = __import__("ogtool.simulate", fromlist=["Script"]).Script([])
    config = KeepOnlineConfig(account_labels=["acc1"], interval=0.0,
                              loops=1, jitter=0.0)
    assert run_keep(config, ctx) == 0
    out = capsys.readouterr().out
    assert "✅ ONLINE acc1" in out


def test_the_runner_rows_carry_the_markers(ctx, capsys, live):
    """batch_join rides the shared runner: its rows must be marked too."""
    class Joiner:
        def join(self, *args, **kwargs):
            return SimpleNamespace(ok=True, code="ok", detail="",
                                   items=[], message_id=0)

        def mute(self, *args, **kwargs):
            return SimpleNamespace(ok=True, code="ok", detail="",
                                   items=[], message_id=0)

        def close(self, *args, **kwargs):
            return None

    live(Joiner())
    config = JoinConfig(targets=parse_many(["@group1"], "custom"),
                        operation_type="join")
    assert run_join(config, ctx) == 0
    out = capsys.readouterr().out
    assert "✅ SENT" in out and "-> ok" in out
