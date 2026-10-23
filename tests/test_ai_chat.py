"""#2 AI原创炒群: the plan, the news seasoning and a model that fails."""

from __future__ import annotations

import random

import pytest

from ogtool.ai import AIError
from ogtool.config import ConfigError
from ogtool.features.ai_chat import AiChatConfig, plan, run


def config_for(**overrides) -> AiChatConfig:
    values = {"targets": ["@g1"], "count": 2}
    values.update(overrides)
    return AiChatConfig(**values)


def test_from_dict_needs_a_target():
    with pytest.raises(ConfigError):
        AiChatConfig.from_dict({"ai_chat": {"topics": "x"}}, None)  # type: ignore[arg-type]


def test_plan_rotates_the_topics_and_counts_per_target():
    config = config_for(targets=["@g1", "@g2"], count=3,
                        topics=["one", "two"])
    lines = plan(config, ["acc1"], random.Random(1))
    assert [line.topic for line in lines] == [
        "one", "two", "one", "two", "one", "two"]
    assert len(lines) == 6
    assert [line.account for line in lines] == ["acc1"] * 6


def test_news_is_inserted_at_one_hundred_percent():
    config = config_for(news_channel="@news", insert_probability=100,
                        news=["breaking story"], count=2)
    lines = plan(config, ["acc1"], random.Random(2))
    assert [line.news for line in lines] == ["breaking story"] * 2


def test_news_stays_out_at_zero_percent():
    config = config_for(news_channel="@news", insert_probability=0,
                        news=["breaking story"], count=3)
    assert [line.news for line in plan(config, ["acc1"])] == [""] * 3


def test_run_uses_the_model_answer(ctx, capsys, fake_ai):
    ctx.ai = fake_ai(lambda prompt, system, config: ("a generated line", 7))
    assert run(config_for(count=1), ctx) == 0
    output = capsys.readouterr().out
    assert "a generated line" in output
    assert "ai calls: 1 (fallback 0)" in output
    assert "PLAN: 1 messages" in output


def test_a_failing_model_does_not_stop_the_task(ctx, capsys, fake_ai):
    def boom(prompt: str, system: str, config):
        raise AIError("AI endpoint timed out")

    ctx.ai = fake_ai(boom)
    assert run(config_for(count=2), ctx) == 0
    output = capsys.readouterr().out
    assert "ai=template" in output
    assert "ai calls: 2 (fallback 2)" in output
