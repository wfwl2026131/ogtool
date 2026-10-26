"""#3's page keys, read by the names the binary actually stores.

``commentMinCount`` / ``commentMaxCount``, ``commentMaxChars``,
``positiveRatioPercent``, ``likeMinCount`` / ``likeMaxCount``, ``likeEmojis``,
``pollScanRecentCount``, plus the four switches that decide when a post counts
as handled: ``commentRequired``, ``likeRequired``, ``commentConcurrentPosts``,
``commentRetryMax``, ``onlyRootForwardedFromChannel``.
"""

from __future__ import annotations

from ogtool.features.ai_comment import CommentConfig, run


def _settings():
    return type("S", (), {"runtime": type("R", (), {
        "clamp_threads": staticmethod(lambda value: value)})()})()


def test_the_reference_names_are_read():
    config = CommentConfig.from_dict(
        {"ai_comment": {
            "targets": "@ch",
            "comment_min_count": 2,
            "comment_max_count": 4,
            "comment_max_chars": 60,
            "positive_ratio_percent": 75,
            "like_min_count": 1,
            "like_max_count": 5,
            "poll_scan_recent_count": 7,
        }},
        _settings(),
    )
    assert (config.per_post_min, config.per_post_max) == (2, 4)
    assert config.max_chars == 60
    assert config.positive_ratio == 75
    assert (config.likes_min, config.likes_max) == (1, 5)
    assert config.poll_recent == 7
    # poll_interval_seconds is gone: the one-shot run has no poll loop, the
    # flag was display-only and was deleted in phase five.


def test_the_old_names_still_work():
    config = CommentConfig.from_dict(
        {"ai_comment": {"targets": "@ch", "per_post_min": 3,
                        "max_chars": 45}},
        _settings(),
    )
    assert config.per_post_min == 3
    assert config.max_chars == 45


def test_like_emojis_is_a_list_with_the_single_name_folded_in():
    config = CommentConfig.from_dict(
        {"ai_comment": {"targets": "@ch",
                        "like_emoji": "🔥",
                        "like_emojis": ["👍", "❤️"]}},
        _settings(),
    )
    assert config.like_emojis == ["🔥", "👍", "❤️"]


def test_a_comma_blob_is_accepted_for_the_emoji_pool():
    config = CommentConfig.from_dict(
        {"ai_comment": {"targets": "@ch", "like_emojis": "👍, ❤️ 🔥"}},
        _settings(),
    )
    assert config.like_emojis == ["👍", "❤️", "🔥"]


def test_the_required_switches_default_like_the_page():
    config = CommentConfig.from_dict({"ai_comment": {"targets": "@ch"}},
                                     _settings())
    assert config.require_comment is True
    assert config.require_like is False
    assert config.only_root_forwarded is False
    assert config.concurrent_posts == 1
    assert config.retry_max == 0


def test_each_like_picks_from_the_pool(ctx, capsys):
    config = CommentConfig(
        targets=["@ch"], likes_min=1, likes_max=1,
        like_emojis=["👍", "🔥"], per_post_min=1, per_post_max=1,
        emoji_probability=0, seed=7, posts=1,
        comment_interval_min=0, comment_interval_max=0,
    )
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "reactions: 👍, 🔥" in output


def test_the_header_names_the_new_switches(ctx, capsys):
    config = CommentConfig(
        targets=["@ch"], require_like=True, only_root_forwarded=True,
        concurrent_posts=3, retry_max=2, per_post_min=1, per_post_max=1,
        posts=1, comment_interval_min=0, comment_interval_max=0, seed=1,
    )
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "concurrent posts: 3" in output
    assert "retry max: 2" in output
    assert "required: comment+like" in output
    assert "only root forwarded: yes" in output


def test_an_out_of_range_sticker_share_is_clamped_with_the_page_ceiling():
    config = CommentConfig.from_dict(
        {"ai_comment": {"targets": "@ch", "sticker_send_percent": 140}},
        _settings(),
    )
    assert config.sticker_send_percent == 100


def test_nothing_about_the_plan_changed_for_a_default_config(ctx, capsys):
    config = CommentConfig(targets=["@ch"], posts=1, seed=3,
                           comment_interval_min=0, comment_interval_max=0)
    assert run(config, ctx) == 0
    assert "Processed:" in capsys.readouterr().out
