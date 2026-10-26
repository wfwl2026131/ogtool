"""#14 is group-call automation, and the dump's own action list says so.

Join / Leave / Speak / Mic off, an "(in call)" state, a "[Muted]" marker and
"success cached group call slug" sit next to ``operation`` and ``intervalMs`` in
``video_manage_config.json``.
"""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features.video_manage import MIC_OFF, SPEAK, VideoConfig, run


def _settings():
    return type("S", (), {"runtime": type("R", (), {
        "clamp_threads": staticmethod(lambda value: value)})()})()


def test_the_page_operation_key_is_read_as_the_action():
    config = VideoConfig.from_dict(
        {"video_manage": {"link": "@g1", "operation": "speak"}},
        _settings())
    assert config.action == SPEAK


def test_the_old_action_key_still_works():
    config = VideoConfig.from_dict(
        {"video_manage": {"link": "@g1", "action": "mic_off"}},
        _settings())
    assert config.action == MIC_OFF


def test_an_unknown_operation_lists_the_allowed_words():
    with pytest.raises(ConfigError) as caught:
        VideoConfig.from_dict(
            {"video_manage": {"link": "@g1", "operation": "upload"}},
            _settings())
    assert "speak" in str(caught.value)


def test_mic_off_joins_the_call_muted(ctx, capsys):
    config = VideoConfig(link="@g1", action=MIC_OFF, interval_ms=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "muted=1" in output
    assert "已闭麦账号" in output


def test_speak_joins_unmuted(ctx, capsys):
    config = VideoConfig(link="@g1", action=SPEAK, interval_ms=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "muted=0" in output


def test_the_call_cache_lives_on_the_sender(ctx, capsys):
    """The page used to keep a synthesized "slug" nobody consumed; the real
    cache is on the TelethonSender now (one GetFullChannel per target, the
    next action reuses the stored call - pinned in test_video_manage.py).
    The page itself carries no cache and prints nothing about slugs."""
    config = VideoConfig(link="@g1", action=SPEAK, interval_ms=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "slug" not in output
    assert not hasattr(config, "slug_cache")
