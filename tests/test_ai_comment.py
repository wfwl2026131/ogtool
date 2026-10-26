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
        self.reacted: list[tuple[str, int, str]] = []
        self.posts = posts or []

    def react(self, account: str, target: object, message_id: int,
              emoji: str = "") -> object:
        from ogtool.senders import SendResult

        self.reacted.append((account, message_id, emoji))
        return SendResult(ok=True, message_id=message_id)

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


def test_a_like_is_a_reaction_the_server_records(ctx, live, fake_ai,
                                                 monkeypatch):
    # The planned like count used to be printed and recorded while nothing was
    # sent: the comment said likes=3 and the post carried no reaction at all.
    from ogtool.features import _common

    monkeypatch.setattr(_common.time, "sleep", lambda seconds: None)
    ctx.ai = fake_ai(lambda prompt, system, config: ("nice post", 3))
    sender = CapturingSender(posts=[(77, "a real post")])
    live(sender)
    config = config_for(posts=2, poll_recent=1, per_post_min=1,
                        per_post_max=1, likes_min=2, likes_max=2)
    assert run(config, ctx) == 0
    assert sender.reacted == [("acc1", 77, "\U0001F44D"),
                              ("acc2", 77, "\U0001F44D")]


def test_one_account_never_reacts_twice_to_the_same_post(ctx, live, fake_ai,
                                                          monkeypatch):
    # Telegram keeps one reaction per account per message; asking one account
    # for three likes can only ever produce one, so the run reports one.
    from ogtool.features import _common

    monkeypatch.setattr(_common.time, "sleep", lambda seconds: None)
    ctx.ai = fake_ai(lambda prompt, system, config: ("nice post", 3))
    sender = CapturingSender(posts=[(77, "a real post")])
    live(sender)
    config = config_for(posts=2, poll_recent=1, per_post_min=1,
                        per_post_max=1, likes_min=3, likes_max=3)
    run(config, ctx)
    accounts = [account for account, _mid, _e in sender.reacted]
    assert accounts == ["acc1", "acc2"], "one reaction per account, no repeats"
    assert len(accounts) == len(set(accounts))


def test_one_lane_per_post_in_plan_order():
    """``commentConcurrentPosts`` counts *posts*, so a post is the work unit."""
    from ogtool.features.ai_comment import CommentSpec, _post_lanes

    specs = [
        CommentSpec(account="a1", target="@t", post_id=10, tone="neutral",
                    likes=0, emoji=False, delay=0),
        CommentSpec(account="a2", target="@t", post_id=10, tone="neutral",
                    likes=0, emoji=False, delay=0),
        CommentSpec(account="a1", target="@t", post_id=11, tone="neutral",
                    likes=0, emoji=False, delay=0),
        CommentSpec(account="a1", target="@u", post_id=10, tone="neutral",
                    likes=0, emoji=False, delay=0),
    ]
    lanes = _post_lanes(specs)
    # Same post id on a different target is a different post.
    assert [[spec.account for spec in lane] for lane in lanes] == [
        ["a1", "a2"], ["a1"], ["a1"]]


def test_a_run_that_neither_comments_nor_likes_is_a_usage_error(ctx, capsys):
    """``required: none`` would print a full plan and do nothing."""
    from ogtool.features.ai_comment import CommentConfig, run

    config = CommentConfig(targets=["@g1"], require_comment=False,
                           require_like=False, messages=[])
    assert run(config, ctx) == 2
    assert "would do nothing" in capsys.readouterr().out


def test_like_only_mode_reacts_without_commenting(ctx, capsys):
    from ogtool.features.ai_comment import CommentConfig, run

    class LikeSender:
        def __init__(self):
            self.sent = 0
            self.reacted = 0

        def send(self, account, target, content):
            self.sent += 1
            raise AssertionError("like-only mode must not comment")

        def react(self, account, target, message_id, reaction):
            from ogtool.senders import SendResult

            self.reacted += 1
            return SendResult(ok=True)

    sender = LikeSender()

    class LiveCtx:
        live = False
        task_id = None
        waited = 0.0

        def __init__(self, inner):
            self.inner = inner
            self.settings = inner.settings
            self.store = inner.store

        def sender(self):
            return sender

        def note(self, message):
            pass

        def bind(self, labels):
            return self.inner.bind(labels)

        @property
        def ai(self):
            return self.inner.ai

    from ogtool.sources import parse_many as parse_sources

    config = CommentConfig(targets=["@g1"], require_comment=False,
                           require_like=True, likes_min=1, likes_max=1,
                           posts=1, per_post_min=1, per_post_max=1,
                           messages=parse_sources(["@g1|Ann|hello"]))
    run(config, LiveCtx(ctx))
    assert sender.sent == 0
    assert sender.reacted >= 1


def test_retry_max_retries_a_failed_comment(ctx, capsys):
    """``commentRetryMax``: a failed comment is worth another try."""
    from ogtool.features.ai_comment import CommentConfig, run
    from ogtool.senders import SendResult
    from ogtool.sources import parse_many as parse_sources

    class FlakySender:
        def __init__(self):
            self.calls = 0

        def send(self, account, target, content):
            self.calls += 1
            if self.calls == 1:
                return SendResult(ok=False, code="FLOOD", detail="slow down")
            return SendResult(ok=True)

        def react(self, account, target, message_id, reaction):
            return SendResult(ok=True)

    sender = FlakySender()

    class LiveCtx:
        live = False
        task_id = None
        waited = 0.0

        def __init__(self, inner):
            self.inner = inner
            self.settings = inner.settings
            self.store = inner.store

        def sender(self):
            return sender

        def note(self, message):
            pass

        def bind(self, labels):
            return self.inner.bind(labels)

        @property
        def ai(self):
            return self.inner.ai

    config = CommentConfig(targets=["@g1"], messages=parse_sources(
        ["@g1|Ann|hello"]), posts=1, per_post_min=1, per_post_max=1,
        retry_max=2, likes_min=0, likes_max=0)
    assert run(config, LiveCtx(ctx)) == 0
    assert sender.calls == 2
    output = capsys.readouterr().out
    assert "retry 1/2" in output
    assert "retries=1" in output


def test_only_root_forwarded_keeps_channel_forwards_only(capsys):
    """``onlyRootForwardedFromChannel`` = 'Only comment channel-forwarded roots'."""
    from ogtool.features.ai_comment import CommentConfig, plan
    from ogtool.sources import SourceMessage

    config = CommentConfig(
        targets=["@c1"], per_post_min=1, per_post_max=1,
        only_root_forwarded=True,
        messages=[
            SourceMessage(peer="@c1", sender="", text="typed", message_id=1),
            SourceMessage(peer="@c1", sender="", text="forwarded", message_id=2,
                          flags=("forwarded", "forwarded_channel")),
        ])
    stats = plan(config, ["acc1"])
    assert stats.total == 1
    assert all(spec.post_id == 2 for spec in stats.specs)
    assert "root filter: only channel-forwarded posts (1 kept, 1 dropped)" \
        in capsys.readouterr().out


def test_only_root_forwarded_with_nothing_marked_comments_on_nothing(capsys):
    from ogtool.features.ai_comment import CommentConfig, plan
    from ogtool.sources import SourceMessage

    config = CommentConfig(targets=["@c1"], only_root_forwarded=True,
                           messages=[SourceMessage(peer="@c1", sender="",
                                                   text="plain",
                                                   message_id=1)])
    stats = plan(config, ["acc1"])
    assert stats.total == 0
    assert "0 kept, 1 dropped" in capsys.readouterr().out


def test_forward_flags_come_from_the_forward_header():
    """The capture marks what the header says, not what the text looks like."""
    from ogtool.senders import _forward_flags

    class Header:
        channel_post = 7
        from_id = None

    class ForwardedFromChannel:
        fwd_from = Header()

    class Typed:
        fwd_from = None

    assert _forward_flags(ForwardedFromChannel()) == ("forwarded",
                                                      "forwarded_channel")
    assert _forward_flags(Typed()) == ()


def test_sticker_share_is_drawn_in_the_plan():
    """``stickerSendPercent``: 'probability of sending a sticker instead of text'."""
    from ogtool.features.ai_comment import CommentConfig, plan

    config = CommentConfig(targets=["@c1"], per_post_min=50, per_post_max=50,
                           posts=1, sticker_send_percent=100, seed=7)
    stats = plan(config, ["acc1"])
    assert stats.total == 50
    assert stats.stickers == 50

    config = CommentConfig(targets=["@c1"], per_post_min=50, per_post_max=50,
                           posts=1, sticker_send_percent=0, seed=7)
    assert plan(config, ["acc1"]).stickers == 0


def test_a_sticker_with_no_set_enabled_goes_out_as_text(ctx, capsys):
    from ogtool.features.ai_comment import CommentConfig, run

    config = CommentConfig(targets=["@c1"], per_post_min=1, per_post_max=1,
                           posts=1, sticker_send_percent=100,
                           comment_interval_min=0, comment_interval_max=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "sticker skipped: no sticker set is enabled" in output
    assert "stickers: 0" in output


def test_a_sticker_is_sent_from_the_enabled_set(ctx, live, capsys):
    """A live sticker comment is a sticker, not text: no AI call, no caption."""
    from ogtool.features.ai_comment import CommentConfig, run
    from ogtool.senders import SendResult

    class StickerSender:
        def __init__(self):
            self.stickers = 0
            self.texts = 0

        def send_sticker(self, account, target, sets, reply_to=0):
            self.stickers += 1
            return SendResult(ok=True, detail=f"set={sets[0]}")

        def send(self, account, target, content):
            self.texts += 1
            return SendResult(ok=True)

        def react(self, account, target, message_id, emoji=""):
            return SendResult(ok=True)

        def history(self, account, target, minutes=0, limit=0):
            return SendResult(ok=False, code="SKIPPED")

        def close(self):
            pass

    sender = StickerSender()
    live(sender)
    config = CommentConfig(targets=["@c1"], per_post_min=2, per_post_max=2,
                           posts=1, sticker_send_percent=100,
                           sticker_sets=["cats"], require_like=False,
                           comment_interval_min=0, comment_interval_max=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert sender.stickers == 2
    assert sender.texts == 0
    assert "stickers: 2" in output


def test_the_seed_reaches_the_emoji_and_the_reaction(ctx, live, fake_ai,
                                                     monkeypatch):
    """--seed promised "a reproducible plan" but only the plan was seeded.

    The body emoji and the reaction emoji drew from the module random, so
    two runs with the same seed disagreed with each other and with the plan.
    """
    from ogtool.features import _common

    monkeypatch.setattr(_common.time, "sleep", lambda seconds: None)
    ctx.ai = fake_ai(lambda prompt, system, config: ("nice post", 3))
    bodies = []
    for _ in range(2):
        sender = CapturingSender(posts=[(5, "a post")])
        live(sender)
        assert run(config_for(posts=1, poll_recent=1, per_post_min=1,
                              per_post_max=1, emoji_probability=100,
                              likes_min=1, likes_max=1, seed=11), ctx) == 0
        bodies.append(sender.sent[0][1].text)
        assert len(sender.reacted) == 1
    assert bodies[0] == bodies[1]
