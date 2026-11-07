"""The external news monitor, verdict by verdict.

The channel line grammar (:1744986), the missing-link refusal
(:1744957), the three filters and their summary line (:1744958), the
focus decay (:1738480-1738482) and the inject roll (:1744985).
"""

from __future__ import annotations

from ogtool.news_monitor import (
    FOCUS_MAX_AGE_SEC,
    NewsChannel,
    NewsItem,
    focus_percent,
    injected,
    parse_channel_line,
    skip_counts,
    skipped_line,
)


def _item(text="hello world", age=0.0, mid=1, src="@news"):
    return NewsItem(source=src, message_id=mid, text=text, age_sec=age)


def test_the_channel_line_splits_into_link_and_two_word_lists():
    channel, error = parse_channel_line("https://t.me/n|war,crash|market,oil")
    assert error == ""
    assert channel is not None
    assert channel.link == "https://t.me/n"
    assert channel.filter_words == ("war", "crash")
    assert channel.contains_words == ("market", "oil")


def test_a_plain_link_is_a_channel_without_filters():
    channel, error = parse_channel_line("https://t.me/n")
    assert error == "" and channel.link == "https://t.me/n"
    assert not channel.has_filters


def test_a_line_without_a_link_is_ignored_whole():
    channel, error = parse_channel_line("|war")
    assert channel is None and error.endswith("missing link.")
    channel, error = parse_channel_line("  ")
    assert channel is None and error.endswith("missing link.")


def test_the_three_filters_count_their_own_skips():
    channel = NewsChannel(link="@n", filter_words=("war",), contains_words=("market",))
    kept, counts = skip_counts(
        [
            _item("old news", age=FOCUS_MAX_AGE_SEC + 1),
            _item("the war room"),
            _item("nothing relevant here"),
            _item("market moves today"),
        ],
        channel,
        max_age_sec=FOCUS_MAX_AGE_SEC,
    )
    assert [item.text for item in kept] == ["market moves today"]
    assert counts == {"stale": 1, "filtered": 1, "contains_miss": 1}


def test_the_skipped_line_carries_all_three_counts():
    line = skipped_line({"stale": 1, "filtered": 2, "contains_miss": 3}, 6)
    assert line == (
        "External news skipped by filters: stale=1 filtered=2 contains_miss=3 (6)"
    )


def test_no_age_check_when_the_max_is_zero():
    channel = NewsChannel(link="@n")
    kept, counts = skip_counts([_item("ancient", age=10**6)], channel, max_age_sec=0.0)
    assert len(kept) == 1 and counts["stale"] == 0


def test_the_focus_decays_by_half_life_and_dies_at_max_age():
    fresh = _item(age=0.0)
    assert focus_percent(fresh, initial=100, half_life_min=60) == 100
    one_half = _item(age=60 * 60)
    assert focus_percent(one_half, initial=100, half_life_min=60) == 50
    expired = _item(age=FOCUS_MAX_AGE_SEC + 1)
    assert focus_percent(expired) == 0
    assert focus_percent(_item(age=10.0), half_life_min=0) == 100


def test_the_inject_roll_is_stable_per_item_and_bounded():
    item = _item(mid=5)
    assert injected(item, 100) is True
    assert injected(item, 0) is False
    assert injected(item, 50) == injected(item, 50)
    rolls = [injected(_item(mid=n), 50) for n in range(50)]
    assert 10 < sum(rolls) < 40


def test_the_ai_chat_news_block_speaks_the_monitor_lines(ctx, live, capsys):
    from ogtool.features.ai_chat import AiChatConfig, run
    from ogtool.senders import SendResult

    class NewsSender:
        def history(self, account, target, minutes=0, limit=0):
            return SendResult(
                ok=True,
                count=2,
                items=[
                    "@c|@n|the war room",
                    "@c|@n|market moves today",
                ],
            )

        def send(self, account, target, content):
            return SendResult(ok=True)

        def close(self):
            pass

    live(NewsSender())
    config = AiChatConfig(
        targets=["@g1"],
        count=1,
        topics=["hi"],
        news_channel="https://t.me/n|war|market",
        interval_min=0,
        interval_max=0,
    )
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert (
        "External news skipped by filters: stale=0 filtered=1 contains_miss=0 (2)"
    ) in out
    assert "news: 1 item(s) read" in out


def test_a_channel_line_without_a_link_ignores_the_news(ctx, live, capsys):
    from ogtool.features.ai_chat import AiChatConfig, run
    from ogtool.senders import SendResult

    class NoNews:
        def history(self, account, target, minutes=0, limit=0):
            return SendResult(ok=True, count=0, items=[])

        def send(self, account, target, content):
            return SendResult(ok=True)

        def close(self):
            pass

    live(NoNews())
    config = AiChatConfig(
        targets=["@g1"],
        count=1,
        topics=["hi"],
        news_channel="|war",
        interval_min=0,
        interval_max=0,
    )
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "External news channel line ignored: missing link." in out
    assert "news: 0 item(s) read" in out
