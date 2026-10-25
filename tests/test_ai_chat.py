"""#2 AI原创炒群: the plan, the news seasoning and a model that fails."""

from __future__ import annotations

import random

import pytest

from ogtool.ai import AIError
from ogtool.cli import build_parser
from ogtool.config import ConfigError
from ogtool.features.ai_chat import AiChatConfig, _news, plan, run


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


def test_news_items_accept_a_list_or_one_per_line():
    assert _news(["a", " ", "b"]) == ["a", "b"]
    assert _news("a\n\nb\n") == ["a", "b"]
    assert _news(None) == []


def test_the_flag_is_news_not_an_abbreviation_of_news_channel():
    args = build_parser().parse_args(
        ["ai_chat", "--news", "story", "--news-channel", "@news"])
    assert args.news == ["story"]
    assert args.news_channel == "@news"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["ai_chat", "--new", "story"])


def test_the_fallback_line_is_the_topic_not_the_prompt(ctx, capsys):
    assert run(config_for(topics=["weekend plans"], insert_probability=100,
                          news=["rain"], news_channel="@news", count=1),
               ctx) == 0
    output = capsys.readouterr().out
    assert "text=weekend plans rain" in output
    assert "[template] \u4f60\u5728" not in output


def test_stickiness_zero_is_the_plain_rotation():
    config = config_for(targets=["@g1"], count=4, topics=["one", "two"],
                        topic_stickiness=0)
    lines = plan(config, ["acc1"], random.Random(3))
    assert [line.topic for line in lines] == ["one", "two", "one", "two"]


def test_full_stickiness_never_advances_after_the_first_topic():
    """100% stickiness means one topic stays the subject for the whole batch."""
    config = config_for(targets=["@g1"], count=4, topics=["one", "two"],
                        topic_stickiness=100)
    lines = plan(config, ["acc1"], random.Random(3))
    assert {line.topic for line in lines} == {"one"}


def test_topic_min_count_stops_sticking_too_early():
    """``topicMinCount`` guarantees that many distinct topics before sticking."""
    config = config_for(targets=["@g1"], count=4, topics=["one", "two", "three"],
                        topic_stickiness=100, topic_min_count=3)
    lines = plan(config, ["acc1"], random.Random(3))
    assert [line.topic for line in lines] == ["one", "two", "three", "three"]
