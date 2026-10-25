"""#2 and #4 read the keys the binary stores, not only my own names.

``ai_chat``: ``topicMinCount``, ``topicStickiness``, ``chatTextMaxCharsLevels``,
``stickerSendPerMille`` / ``emojiSendPerMille``, ``replyEnabled`` /
``allowFollowUp``, ``patternRefreshSeconds``.
``ai_clone``: ``pairs``, ``passive``, ``onlyRootForwardedFromChannel``,
``lastSourceMessageId``, ``totalCloned``.
"""

from __future__ import annotations

from ogtool.features.ai_chat import AiChatConfig
from ogtool.features.ai_chat import run as run_chat
from ogtool.features.ai_clone import CloneConfig, mapping_of
from ogtool.features.ai_clone import run as run_clone


def _settings():
    return type("S", (), {"runtime": type("R", (), {
        "clamp_threads": staticmethod(lambda value: value)})()})()


def test_ai_chat_reads_the_page_keys():
    config = AiChatConfig.from_dict(
        {"ai_chat": {
            "targets": "@g",
            "topic_min_count": 3,
            "topic_stickiness": 40,
            "chat_text_max_chars_levels": [40, 80, 120],
            "sticker_send_per_mille": 25,
            "emoji_send_per_mille": 120,
            "reply_enabled": True,
            "allow_follow_up": True,
            "pattern_refresh_seconds": 900,
        }},
        _settings(),
    )
    assert config.topic_min_count == 3
    assert config.topic_stickiness == 40
    assert config.text_max_chars_levels == [40, 80, 120]
    assert (config.sticker_send_per_mille, config.emoji_send_per_mille) == (25, 120)
    assert config.reply_enabled is True
    assert config.allow_follow_up is True
    assert config.pattern_refresh_seconds == 900


def test_ai_chat_stickiness_is_clamped_to_a_percentage():
    config = AiChatConfig.from_dict(
        {"ai_chat": {"targets": "@g", "topic_stickiness": 250}},
        _settings(),
    )
    assert config.topic_stickiness == 100


def test_ai_chat_ignores_non_numeric_text_levels():
    config = AiChatConfig.from_dict(
        {"ai_chat": {"targets": "@g",
                     "chat_text_max_chars_levels": [40, "long", None]}},
        _settings(),
    )
    assert config.text_max_chars_levels == [40]


def test_the_chat_header_names_the_new_fields(ctx, capsys):
    config = AiChatConfig(targets=["@g"], count=1, interval_min=0,
                          interval_max=0, topic_min_count=2,
                          text_max_chars_levels=[80],
                          emoji_send_per_mille=100, reply_enabled=True)
    assert run_chat(config, ctx) == 0
    output = capsys.readouterr().out
    assert "topic min count: 2" in output
    assert "text levels: 80" in output
    assert "per mille: sticker 0 / emoji 100" in output
    assert "replies: on" in output


def test_clone_pairs_accept_objects_and_strings():
    mapping = mapping_of({"pairs": [
        {"sourceRaw": "@s1", "targetRaw": "@t1", "pairIndex": 0},
        "@s2=@t2",
    ]})
    assert mapping == {"@s1": "@t1", "@s2": "@t2"}


def test_clone_mapping_still_works_alone():
    assert mapping_of({"mapping": "@s1=@t1"}) == {"@s1": "@t1"}


def test_clone_pairs_win_over_a_stale_mapping_entry():
    mapping = mapping_of({"mapping": "@s1=@old",
                          "pairs": [{"source": "@s1", "target": "@new"}]})
    assert mapping["@s1"] == "@new"


def test_clone_reads_the_passive_switches():
    config = CloneConfig.from_dict(
        {"ai_clone": {"targets": "@t1", "sources": "@s1",
                      "passive": True,
                      "only_root_forwarded_from_channel": True,
                      "last_source_message_id": 42,
                      "total_cloned": 7}},
        _settings(),
    )
    assert config.passive is True
    assert config.only_root_forwarded is True
    assert config.last_source_message_id == 42
    assert config.total_cloned == 7


def test_the_clone_header_names_the_switches(ctx, capsys):
    config = CloneConfig(targets=["@t1"], sources=["@s1"], passive=True,
                         last_source_message_id=99, max_items=5,
                         post_interval_min=0, post_interval_max=0)
    assert run_clone(config, ctx) == 0
    output = capsys.readouterr().out
    assert "passive: on" in output
    assert "resume after: 99" in output
