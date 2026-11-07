"""The FreeMe group-cleanup engine, judged line by line.

The page's own contract (:1742835-1742845): the keep set, the service
short-circuit, the whitelist separators, the 5-minute mute window and
the 0/required clauses on the two switches.
"""

from __future__ import annotations

from ogtool.freeme_cleanup import (
    MUTE_DEDUPE_SEC,
    CleanerTarget,
    CleanupConfig,
    GroupCleaner,
    bot_api_url,
    delete_verdict,
    mute_due,
    parse_whitelist,
    rate_limit_wait,
)


def _config(**overrides) -> CleanupConfig:
    values: dict = {"enabled": True, "bot_token": "12:abc"}
    values.update(overrides)
    return CleanupConfig(**values)


def _message(**overrides) -> CleanerTarget:
    values: dict = {
        "chat_id": "-1001",
        "message_id": 7,
        "sender_id": "900",
        "sender_username": "stranger",
    }
    values.update(overrides)
    return CleanerTarget(**values)


def test_the_whitelist_reads_all_three_separators():
    assert parse_whitelist("a,b|c\nd") == ["a", "b", "c", "d"]
    assert parse_whitelist("") == []
    assert parse_whitelist(" @x , y ") == ["@x", "y"]


def test_the_keep_set_is_the_page_own():
    config = _config(whitelist_text="100,@stranger")
    assert delete_verdict(config, _message(is_worker=True))[0] is False
    assert delete_verdict(config, _message(is_owner=True))[0] is False
    assert (
        delete_verdict(config, _message(sender_id="77", sender_username="other"))[0]
        is True
    )
    config.admin_user_ids = ["77"]
    assert delete_verdict(config, _message(sender_id="77"))[0] is False
    assert delete_verdict(config, _message(sender_id="100"))[0] is False
    assert (
        delete_verdict(config, _message(sender_id="55", sender_username="stranger"))[0]
        is False
    )
    assert (
        delete_verdict(config, _message(sender_id="55", sender_username="other"))[0]
        is True
    )


def test_a_service_message_is_deleted_even_from_a_worker():
    config = _config()
    verdict, reason = delete_verdict(config, _message(is_service=True, is_worker=True))
    assert verdict and reason == "service message deleted directly"


def test_the_switches_gate_the_whole_engine():
    verdict, reason = delete_verdict(_config(enabled=False), _message())
    assert not verdict and reason == "cleanup disabled"
    verdict, reason = delete_verdict(_config(bot_token=""), _message())
    assert not verdict and reason == "Bot token is required"


def test_the_mute_window_dedupes_to_five_minutes():
    last: dict[str, float] = {}
    assert mute_due(last, "u1", now=0.0)
    assert not mute_due(last, "u1", now=MUTE_DEDUPE_SEC - 1)
    assert mute_due(last, "u1", now=MUTE_DEDUPE_SEC)
    assert mute_due(last, "u2", now=0.0)


def test_the_local_bot_switch_picks_the_endpoint_family():
    assert (
        bot_api_url("12:abc", "deleteMessage")
        == "https://api.telegram.org/bot12:abc/deleteMessage"
    )
    assert bot_api_url("", "deleteMessage") == ""


def test_the_rate_limit_waits_out_the_rolling_second():
    log = [0.0, 0.1, 0.2]
    assert rate_limit_wait(log, 10, now=0.3) == 0.0
    tight = [0.0, 0.2, 0.4]
    assert rate_limit_wait(tight, 3, now=0.5) > 0.0
    assert rate_limit_wait(tight, 0, now=0.5) == 0.0


def test_the_cleaner_round_acts_through_its_seams():
    deleted: list[tuple[str, int]] = []
    muted: list[tuple[str, str]] = []
    config = _config(mute_external=True, admin_user_ids=["55"])
    cleaner = GroupCleaner(
        config,
        delete=lambda chat, mid: deleted.append((chat, mid)),
        mute=lambda chat, uid: muted.append((chat, uid)),
        administrators=lambda: ["66"],
        clock=lambda: 1000.0,
    )
    messages = [
        _message(message_id=1, sender_id="900"),
        _message(message_id=2, sender_id="55"),  # admin: kept
        _message(message_id=3, sender_id="66"),  # fetched admin: kept
        _message(message_id=4, is_service=True, sender_id="900"),
        _message(message_id=5, sender_id="901"),
    ]
    acts = cleaner.run(messages)
    assert (1, "deleted (external message)") in acts
    assert (4, "deleted (service message deleted directly)") in acts
    assert (5, "muted (external message)") in acts
    assert deleted == [("-1001", 1), ("-1001", 4), ("-1001", 5)]
    assert muted == [("-1001", "900"), ("-1001", "901")]


def test_an_off_or_tokenless_cleaner_does_nothing():
    deleted: list[tuple[str, int]] = []
    cleaner = GroupCleaner(
        _config(enabled=False), delete=lambda chat, mid: deleted.append((chat, mid))
    )
    assert cleaner.run([_message()]) == []
    assert deleted == []
