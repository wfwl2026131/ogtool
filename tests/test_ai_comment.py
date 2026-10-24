"""#3 AI智能评论: the proportions are planned, so a dry run can be checked."""

from __future__ import annotations

import random

import pytest

from ogtool.ai import AIError
from ogtool.config import ConfigError
from ogtool.features.ai_comment import (
    CommentConfig,
    _clip,
    plan,
    posts_for,
    run,
)


def config_for(**overrides) -> CommentConfig:
    values = {"targets": ["@chan1"], "posts": 50, "poll_recent": 50,
              "per_post_min": 2, "per_post_max": 2, "seed": 11}
    values.update(overrides)
    return CommentConfig(**values)


def test_from_dict_needs_a_target():
    with pytest.raises(ConfigError):
        CommentConfig.from_dict({"ai_comment": {"posts": 1}}, None)  # type: ignore[arg-type]


def test_plan_lands_inside_the_configured_ratios():
    config = config_for(positive_ratio=30, emoji_probability=20,
                        likes_min=1, likes_max=5)
    stats = plan(config, ["acc1"], random.Random(11))
    assert stats.total == 100
    assert abs(stats.ratio(stats.positive) - 30) <= 10
    assert abs(stats.ratio(stats.emoji) - 20) <= 10
    assert all(1 <= spec.likes <= 5 for spec in stats.specs)


def test_comments_per_post_stay_inside_the_range():
    config = config_for(per_post_min=1, per_post_max=3, posts=10,
                        poll_recent=10)
    stats = plan(config, ["acc1"], random.Random(5))
    per_post: dict[int, int] = {}
    for spec in stats.specs:
        per_post[spec.post_id] = per_post.get(spec.post_id, 0) + 1
    assert set(per_post.values()) <= {1, 2, 3}
    assert len(per_post) == 10


def test_posts_are_synthetic_when_nothing_was_supplied():
    posts = posts_for(config_for(posts=3, poll_recent=3), "@chan1")
    assert [post.message_id for post in posts] == [1, 2, 3]


def test_clip_respects_the_limit():
    assert _clip("a" * 100, 10) == "a" * 10
    assert _clip("short", 10) == "short"
    assert _clip("two   lines", 20) == "two lines"


def test_run_prints_the_ratios(ctx, capsys, fake_ai):
    ctx.ai = fake_ai(lambda prompt, system, config: ("nice post", 3))
    assert run(config_for(), ctx) == 0
    output = capsys.readouterr().out
    assert "positive:" in output
    assert "emoji:" in output
    assert "PLAN: 100 comments" in output


def test_a_failing_model_still_produces_comments(ctx, capsys, fake_ai):
    def boom(prompt: str, system: str, config):
        raise AIError("AI endpoint unreachable")

    ctx.ai = fake_ai(boom)
    assert run(config_for(posts=2, poll_recent=2, per_post_min=1,
                          per_post_max=1), ctx) == 0
    output = capsys.readouterr().out
    assert "fallback 2" in output
    assert "ok: 2" in output


class CapturingSender:
    """Keeps the Content the page handed over, so the thread id is visible."""

    def __init__(self, posts: list[tuple[int, str]] | None = None) -> None:
        self.sent: list[tuple[str, object]] = []
        self.posts = posts or []

    def history(self, account: str, target: object, minutes: int = 0,
                limit: int = 0):
        from ogtool.senders import SendResult

        return SendResult(ok=True, count=len(self.posts), posts=list(self.posts))

    def send(self, account: str, target: object, content: object):
        from ogtool.senders import SendResult

        self.sent.append((account, content))
        return SendResult(ok=True, message_id=1)

    def close(self) -> None:
        pass


def test_a_live_run_replies_to_the_posts_the_chat_actually_has(
        ctx, live, fake_ai, monkeypatch):
    # The offline stand-ins are numbered 1..N, which on a real chat means the
    # group service message; a live run has to read the ids it is commenting on.
    from ogtool.features import _common

    monkeypatch.setattr(_common.time, "sleep", lambda seconds: None)
    ctx.ai = fake_ai(lambda prompt, system, config: ("nice post", 3))
    sender = CapturingSender(posts=[(77, "a real post")])
    live(sender)
    config = config_for(posts=2, poll_recent=1, per_post_min=1,
                        per_post_max=1)
    assert run(config, ctx) == 0
    assert [content.reply_to for _account, content in sender.sent] == [77]


def test_a_comment_is_answered_under_its_post(ctx, live, fake_ai, monkeypatch):
    from ogtool.features import _common

    slept: list[float] = []
    monkeypatch.setattr(_common.time, "sleep",
                        lambda seconds: slept.append(seconds))
    ctx.ai = fake_ai(lambda prompt, system, config: ("nice post", 3))
    sender = CapturingSender()
    live(sender)
    config = config_for(posts=2, poll_recent=2, per_post_min=1,
                        per_post_max=1)
    assert run(config, ctx) == 0
    assert [content.reply_to for _account, content in sender.sent] == [1, 2]
    assert sum(slept) > 0, "a live run has to keep the comment interval"
