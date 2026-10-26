"""#14 video manage: join, leave, stop and the progress counter."""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features.video_manage import JOIN, LEAVE, STOP, VideoConfig, run
from ogtool.targets import parse_many


def test_join_reports_progress_for_every_account(ctx, capsys):
    config = VideoConfig(link="@video", action=JOIN)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "进度：1/2" in output and "进度：2/2" in output
    assert "已加入账号：acc1, acc2" in output


def test_leave_uses_the_same_counter(ctx, capsys):
    config = VideoConfig(link="@video", action=LEAVE)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "DRY-RUN leave" in output
    # The list holds whichever action ran: after a leave it is not "joined".
    assert "已退出账号：acc1, acc2" in output
    assert "已加入账号" not in output


def test_flood_on_leave_is_counted(ctx, scripted, capsys):
    scripted(["PEER_FLOOD", "PEER_FLOOD"])
    config = VideoConfig(link="@video", action=LEAVE)
    assert run(config, ctx) == 1
    output = capsys.readouterr().out
    assert "2 failed" in output


def test_stop_before_any_account(ctx, capsys):
    config = VideoConfig(link="@video", action=STOP)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "stop requested before the first account" in output


def test_missing_link_is_a_config_error():
    with pytest.raises(ConfigError):
        VideoConfig.from_dict({}, None)  # type: ignore[arg-type]


def test_unknown_action_is_a_config_error():
    with pytest.raises(ConfigError):
        VideoConfig.from_dict({"video_manage": {"link": "@g", "action": "fly"}},
                              None)  # type: ignore[arg-type]


def test_a_live_run_waits_the_operation_interval(ctx, live, ok_sender, slept):
    live(ok_sender)
    config = VideoConfig(link="@g1", interval_ms=250)
    assert run(config, ctx) == 0
    # Two accounts from the pool fixture, so one gap of 250 ms.
    assert slept == [0.25]


def test_a_dry_run_operates_without_waiting(ctx, slept):
    config = VideoConfig(link="@g1", interval_ms=250)
    assert run(config, ctx) == 0
    assert slept == []


def test_a_second_call_action_reuses_the_resolved_call(ctx, live, monkeypatch):
    """The page's "cached group call slug" used to be a synthesized string
    nobody consumed. The cache now lives on the sender: one GetFullChannel
    lookup per target, the second action joins with the stored call."""
    from ogtool.senders import TelethonSender

    class CallClient:
        def __init__(self) -> None:
            self.lookups = 0
            self.joins = 0

        def get_me(self):
            return type("Me", (), {"id": 1})()

        def is_user_authorized(self):
            return True

        def __call__(self, request):
            if "GetFullChannel" in type(request).__name__:
                self.lookups += 1
                call = type("Call", (), {"id": 77, "access_hash": 5})()
                full = type("Full", (), {"call": call})()
                chat = type("Chat", (), {})()
                return type("Res", (),
                            {"chats": [chat], "full_chat": full})()
            if "JoinGroupCall" in type(request).__name__:
                self.joins += 1
            return type("Ok", (), {})()

        def get_entity(self, peer):
            return type("Chat", (), {"id": 9, "title": "g"})()

        def get_input_entity(self, peer):
            return type("In", (), {})()

    sender = TelethonSender(ctx.settings)
    client = CallClient()
    sender._clients["acc1"] = client
    target = parse_many(["@g1"])[0]
    first = sender.join_call("acc1", target)
    second = sender.join_call("acc1", target)
    assert first.ok and second.ok
    assert client.lookups == 1  # resolved once, reused
    assert client.joins == 2  # joined twice on the same call
