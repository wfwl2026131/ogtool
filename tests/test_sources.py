"""Source messages: the ``peer|sender|text|media`` lines and the filters."""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.sources import (
    Collected,
    by_peer,
    from_config,
    keep,
    parse_entry,
    parse_many,
    parse_replacements,
    replace_keywords,
    synthetic,
)


def test_entry_without_media():
    message = parse_entry("@src | @alice | hello world")
    assert (message.peer, message.sender, message.text) == (
        "@src", "@alice", "hello world")
    assert message.media == ""


def test_entry_with_media():
    message = parse_entry("@src|@bob|photo|pic.jpg")
    assert message.text == "photo"
    assert message.media == "pic.jpg"


def test_entry_needs_three_fields():
    with pytest.raises(ConfigError):
        parse_entry("@src|@alice")
    with pytest.raises(ConfigError):
        parse_entry("")


def test_many_numbers_the_messages():
    messages = parse_many(["@s|@a|one", "@s|@b|two"])
    assert [message.message_id for message in messages] == [1, 2]


def test_from_config_reads_a_list_and_a_file(tmp_path):
    path = tmp_path / "src.txt"
    path.write_text("@s|@c|from file\n", encoding="utf-8")
    messages = from_config({
        "messages": ["@s|@a|inline"],
        "messages_files": [str(path)],
    })
    assert [message.sender for message in messages] == ["@a", "@c"]


def test_keyword_filter_is_case_insensitive():
    message = parse_entry("@s|@a|Buy CHEAP coins")
    assert keep(message, ["cheap"]) is False
    assert keep(message, ["expensive"]) is True


def test_replacements_are_parsed_and_applied():
    rules = parse_replacements(["100=200", "brand=name"])
    assert replace_keywords("100 brand", rules) == "200 name"
    with pytest.raises(ConfigError):
        parse_replacements(["no separator"])


def test_by_peer_keeps_only_the_named_chats():
    messages = parse_many(["@a|@x|1", "@b|@y|2"])
    assert [m.peer for m in by_peer(messages, ["@b"])] == ["@b"]
    assert len(by_peer(messages, [])) == 2


def test_synthetic_posts_are_numbered():
    posts = synthetic("@chan", 3)
    assert [post.message_id for post in posts] == [1, 2, 3]
    assert posts[0].text.startswith("post 1")


def test_collected_counts_each_reason():
    collected = Collected(scanned=3)
    collected.drop("keyword")
    collected.drop("keyword")
    collected.drop("sender")
    assert collected.dropped == 3
    assert "keyword=2" in collected.summary()
