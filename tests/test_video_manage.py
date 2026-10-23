"""#14 video manage: join, leave, stop and the progress counter."""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features.video_manage import JOIN, LEAVE, STOP, VideoConfig, run


def test_join_reports_progress_for_every_account(ctx, capsys):
    config = VideoConfig(link="@video", action=JOIN)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "进度：1/2" in output and "进度：2/2" in output
    assert "已加入账号：acc1, acc2" in output


def test_leave_uses_the_same_counter(ctx, capsys):
    config = VideoConfig(link="@video", action=LEAVE)
    assert run(config, ctx) == 0
    assert "DRY-RUN leave" in capsys.readouterr().out


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
