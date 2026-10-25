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


def test_pattern_refresh_batch_has_the_requested_length():
    from ogtool.features.ai_chat import refreshed

    config = config_for(targets=["@g1"], count=3, topics=["a", "b"])
    batch = refreshed(config, ["acc1"], 5)
    assert len(batch) == 5
    assert all(line.target == "@g1" for line in batch)


def test_the_plan_refreshes_the_pattern_on_the_timer(ctx, capsys):
    """``patternRefreshSeconds`` re-asks for the wording on a timer.

    The refresh is decided on the plan's own clock (``waited``), so a dry run
    shows exactly where a live run would re-plan - which is the point of a dry
    run for a timing knob.
    """
    from ogtool.features.ai_chat import AiChatConfig, run

    config = AiChatConfig(targets=["@g1"], count=3, topics=["a", "b"],
                          interval_min=2.0, interval_max=2.0,
                          pattern_refresh_seconds=1)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "refresh: topic prompt changed after 2s, 2 line(s) replanned" in output


def test_without_a_refresh_interval_nothing_is_replanned(ctx, capsys):
    from ogtool.features.ai_chat import AiChatConfig, run

    config = AiChatConfig(targets=["@g1"], count=3, topics=["a", "b"],
                          interval_min=2.0, interval_max=2.0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "topic prompt changed" not in output
    assert "refresh: -" in output


def test_clip_uses_one_of_the_levels_and_cuts_on_a_word():
    from ogtool.features.ai_chat import clip

    text = "one two three four five six seven"
    assert clip(text, [12]) == "one two"
    assert clip(text, []) == text
    assert clip(text, [0]) == text
    assert len(clip(text, [9])) <= 9


def test_reply_enabled_answers_the_chat(ctx, live, capsys):
    """``replyEnabled`` turns one-way chatter into a conversation."""
    from ogtool.features.ai_chat import AiChatConfig, run
    from ogtool.senders import SendResult

    class ChatSender:
        def __init__(self):
            self.sent: list[tuple[str, int]] = []

        def history(self, account, target, minutes=0, limit=0):
            result = SendResult(ok=True)
            result.items = ["@g1|Bob|are you there",
                            "@g1|acc1|talking to myself"]
            return result

        def send(self, account, target, content):
            self.sent.append((account, content.reply_to))
            return SendResult(ok=True)

        def close(self):
            pass

    sender = ChatSender()
    live(sender)
    config = AiChatConfig(targets=["@g1"], count=1, topics=["hello"],
                          interval_min=0, interval_max=0, reply_enabled=True,
                          allow_follow_up=True)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    # The first send is the planned chatter (no reply target); only Bob's line
    # is answered - the pool's own message is filtered by author.
    replies = [reply_to for _, reply_to in sender.sent if reply_to]
    assert len(replies) == 1
    assert "replies: 1 answered, 0 failed" in output


def test_without_follow_up_a_sender_gets_one_answer(ctx, live, capsys):
    from ogtool.features.ai_chat import AiChatConfig, run
    from ogtool.senders import SendResult

    class ChatSender:
        def __init__(self):
            self.sent = 0

        def history(self, account, target, minutes=0, limit=0):
            result = SendResult(ok=True)
            result.items = ["@g1|Bob|first", "@g1|Bob|second"]
            return result

        def send(self, account, target, content):
            if content.reply_to:
                self.sent += 1
            return SendResult(ok=True)

        def close(self):
            pass

    sender = ChatSender()
    live(sender)
    config = AiChatConfig(targets=["@g1"], count=0, topics=["hello"],
                          interval_min=0, interval_max=0, reply_enabled=True)
    assert run(config, ctx) == 0
    assert sender.sent == 1


def test_a_dry_run_says_it_answers_nothing(ctx, capsys):
    from ogtool.features.ai_chat import AiChatConfig, run

    config = AiChatConfig(targets=["@g1"], count=1, topics=["hello"],
                          interval_min=0, interval_max=0, reply_enabled=True)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "a dry run reads no history" in output
    assert "replies: 0 answered" in output


def test_emoji_per_mille_is_a_chance_not_a_cap():
    """``emojiSendPerMille=1000`` always adds a face, ``0`` never does."""
    import random

    from ogtool.features.ai_chat import BODY_EMOJI, with_emoji

    for _ in range(20):
        line = with_emoji("hello", 1000, random.Random(1))
        assert line.startswith("hello ")
        assert line.split()[-1] in BODY_EMOJI
    for _ in range(20):
        assert with_emoji("hello", 0) == "hello"


def test_a_low_per_mille_leaves_most_lines_alone():
    import random

    from ogtool.features.ai_chat import with_emoji

    rng = random.Random(7)
    lines = [with_emoji("hello", 1, rng) for _ in range(2000)]
    decorated = sum(1 for line in lines if line != "hello")
    assert 0 < decorated < 20


def test_the_news_channel_fills_the_pool_in_a_live_run(ctx, live, capsys):
    """``newsChannel``: the page reads its items from the channel, live only."""
    from ogtool.features.ai_chat import AiChatConfig, run
    from ogtool.senders import SendResult

    class NewsSender:
        def __init__(self):
            self.sent = 0

        def history(self, account, target, minutes=0, limit=0):
            result = SendResult(ok=True)
            result.items = ["@news|chan|market is up 5% today"]
            return result

        def send(self, account, target, content):
            self.sent += 1
            return SendResult(ok=True)

        def close(self):
            pass

    live(NewsSender())
    config = AiChatConfig(targets=["@g1"], count=1, topics=["hello"],
                          interval_min=0, interval_max=0,
                          news_channel="@news", insert_probability=100)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "news: 1 item(s) read from @news" in output


def test_a_dry_run_does_not_read_the_news_channel(ctx, capsys):
    from ogtool.features.ai_chat import AiChatConfig, run

    config = AiChatConfig(targets=["@g1"], count=1, topics=["hello"],
                          interval_min=0, interval_max=0,
                          news_channel="@news", insert_probability=100)
    assert run(config, ctx) == 0
    assert "read from @news" not in capsys.readouterr().out
