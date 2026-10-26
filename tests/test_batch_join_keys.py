"""#8 reads ``batch_join_config.json`` by the keys the binary stores.

``operationType`` (join / leave) and ``leaveTargets`` decide the action;
``joinConcurrency`` and ``maxJoinedChatsPerAccount`` decide the shape of the
run; ``perAccountJoinIntervalMinSec`` / ``...MaxSec`` (and their ``...Ms``
spellings) decide the pace; ``leaveMode``, ``muteChannels``, ``mutePrivate``,
``leaveIntervalSec`` and ``muteIntervalSec`` are the leave-side policy;
``autoDownload*`` / ``autoPlay*`` are the media policy that rides along.
"""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features.batch_join import JoinConfig, run


def _settings():
    return type("S", (), {"runtime": type("R", (), {
        "clamp_threads": staticmethod(lambda value: value)})()})()


def test_operation_type_accepts_the_two_words():
    assert JoinConfig.from_dict(
        {"batch_join": {"targets": "@g", "operation_type": "leave"}},
        _settings()).operation_type == "leave"
    assert JoinConfig.from_dict(
        {"batch_join": {"targets": "@g"}},
        _settings()).operation_type == "join"


def test_an_unknown_operation_is_refused():
    with pytest.raises(ConfigError):
        JoinConfig.from_dict(
            {"batch_join": {"targets": "@g", "operation_type": "leave-all"}},
            _settings())


def test_leave_targets_are_their_own_list():
    config = JoinConfig.from_dict(
        {"batch_join": {"targets": "@joinme", "operation_type": "leave",
                        "leave_targets": "@old1,@old2"}},
        _settings())
    assert [target.ref for target in config.leave_targets] == ["@old1", "@old2"]


def test_the_seconds_and_milliseconds_spellings_mean_the_same_thing():
    seconds = JoinConfig.from_dict(
        {"batch_join": {"targets": "@g",
                        "per_account_join_interval_min_sec": 3,
                        "per_account_join_interval_max_sec": 9}},
        _settings())
    millis = JoinConfig.from_dict(
        {"batch_join": {"targets": "@g",
                        "per_account_join_interval_min_ms": 3000,
                        "per_account_join_interval_max_ms": 9000}},
        _settings())
    assert (seconds.per_account_interval_min,
            seconds.per_account_interval_max) == (3.0, 9.0)
    assert (millis.per_account_interval_min,
            millis.per_account_interval_max) == (3.0, 9.0)


def test_the_media_policy_is_read():
    # The auto-download / auto-play toggles are gone: they configured the
    # reference client's media autoload preferences, a GUI concept with no
    # CLI counterpart, and the join page never downloaded media.
    config = JoinConfig.from_dict(
        {"batch_join": {"targets": "@g",
                        "auto_download_scope_group": True,
                        "auto_play_videos": True}},
        _settings())
    assert not hasattr(config, "auto_download_scope_group")
    assert not hasattr(config, "auto_play_videos")
    assert not hasattr(config, "auto_play_gifs")
    assert not hasattr(config, "auto_play_limit_mb")


def test_the_leave_policy_is_read():
    # leave_mode is gone: leaving is chosen by operation_type and the sender
    # picks the request per chat kind, so the mode string restated nothing.
    config = JoinConfig.from_dict(
        {"batch_join": {"targets": "@g", "leave_mode": "all",
                        "mute_channels": True, "mute_private": True,
                        "mute_interval_sec": 4, "leave_interval_sec": 7}},
        _settings())
    assert not hasattr(config, "leave_mode")
    assert config.mute_channels and config.mute_private
    assert (config.mute_interval, config.leave_interval) == (4.0, 7.0)


def test_a_leave_run_calls_leave(ctx, capsys):
    config = JoinConfig(operation_type="leave",
                        leave_targets=[__import__("ogtool.targets",
                                                  fromlist=["Target"]).Target(
                            "group", "@old1")],
                        targets=[], request_interval=0,
                        request_timeout=1, account_interval=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "operation: leave" in output
    assert "DRY-RUN leave" in output


def test_a_leave_run_without_leave_targets_fails_like_an_empty_join(ctx,
                                                                    capsys):
    config = JoinConfig(operation_type="leave", targets=[], leave_targets=[])
    assert run(config, ctx) == 2
    assert "no leave targets" in capsys.readouterr().out


def test_the_media_policy_line_is_gone(ctx, capsys):
    from ogtool.targets import Target

    config = JoinConfig(targets=[Target("group", "@g")],
                        request_interval=0, request_timeout=1,
                        account_interval=0)
    assert run(config, ctx) == 0
    assert "media policy" not in capsys.readouterr().out


def test_mute_and_concurrency_reach_the_header(ctx, capsys):
    from ogtool.targets import Target

    config = JoinConfig(targets=[Target("group", "@g")], mute_channels=True,
                        join_concurrency=2, max_joined_chats=100,
                        request_interval=0, request_timeout=1,
                        account_interval=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "mute: channels" in output
    assert "concurrency: 2" in output
    assert "max joined: 100" in output
