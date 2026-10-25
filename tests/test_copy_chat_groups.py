"""The copy-chat keys the string dump lists after the five ignore switches.

``keywordReplacementGroups`` gives a target its own replacement set,
``sourcesPerTarget`` slices the capture in multi-to-one mode,
``crossoverMessageId`` starts a passive run after a known message, and
``dailyTextLimitPerAccount`` / ``collectionIntervalMinutes`` are the two caps
and the timer that were missing.
"""

from __future__ import annotations

from ogtool.features.copy_chat import (
    CopyConfig,
    plan_targets,
    replacement_groups,
    sources_for_target,
)
from ogtool.sources import SourceMessage


def _message(message_id: int, peer: str = "@src", text: str = "hello"):
    return SourceMessage(peer=peer, sender="alice", text=text,
                         message_id=message_id)


def test_replacement_groups_parse_one_list_per_block():
    groups = replacement_groups([["buy=purchase"], "cheap=affordable"])
    assert groups == [
        [("buy", "purchase")],
        [("cheap", "affordable")],
    ]


def test_no_replacement_groups_means_an_empty_list():
    assert replacement_groups(None) == []
    assert replacement_groups([]) == []


def test_a_target_uses_its_own_replacement_group():
    config = CopyConfig(
        targets=["@t1", "@t2"],
        sources=["@src"],
        messages=[_message(1, text="buy cheap")],
        replacement_groups=[[("buy", "purchase")], [("cheap", "affordable")]],
        interval_min=0,
        interval_max=0,
    )
    plans = plan_targets(config, ["acc1"])
    assert plans[0].collected.kept[0].text == "purchase cheap"
    assert plans[1].collected.kept[0].text == "buy affordable"


def test_without_groups_the_global_replacements_apply():
    config = CopyConfig(
        targets=["@t1"],
        sources=["@src"],
        messages=[_message(1, text="buy")],
        replacements=[("buy", "purchase")],
        interval_min=0,
        interval_max=0,
    )
    plans = plan_targets(config, ["acc1"])
    assert plans[0].collected.kept[0].text == "purchase"


def test_sources_per_target_slices_the_source_list():
    chunks = sources_for_target([_message(1), _message(2)],
                                ["@a", "@b", "@c"], 2)
    assert chunks == [["@a", "@b"], ["@c"]]


def test_a_zero_slice_keeps_every_source():
    messages = [_message(1)]
    assert sources_for_target(messages, ["@a"], 0) == messages


def test_each_target_sees_its_own_slice():
    config = CopyConfig(
        targets=["@t1", "@t2"],
        sources=["@a", "@b"],
        messages=[_message(1, peer="@a", text="from a"),
                  _message(2, peer="@b", text="from b")],
        sources_per_target=1,
        interval_min=0,
        interval_max=0,
    )
    plans = plan_targets(config, ["acc1"])
    assert [m.text for m in plans[0].collected.kept] == ["from a"]
    assert [m.text for m in plans[1].collected.kept] == ["from b"]


def test_the_crossover_message_is_not_copied_again():
    config = CopyConfig(
        targets=["@t1"],
        sources=["@src"],
        messages=[_message(1), _message(2), _message(3)],
        crossover_message_id=2,
        interval_min=0,
        interval_max=0,
    )
    plans = plan_targets(config, ["acc1"])
    assert [m.message_id for m in plans[0].collected.kept] == [3]


def test_the_new_fields_default_to_off():
    config = CopyConfig()
    assert config.sources_per_target == 0
    assert config.daily_text_limit_per_account == 0
    assert config.collection_interval_minutes == 0
    assert config.crossover_message_id == 0
    assert config.replacement_groups == []


def test_the_speech_interval_reads_the_pages_seconds_names():
    config = CopyConfig.from_dict(
        {"copy_chat": {"targets": "@t1",
                       "min_speech_interval_seconds": 3,
                       "max_speech_interval_seconds": 9}},
        None,  # type: ignore[arg-type]
    )
    assert (config.interval_min, config.interval_max) == (3.0, 9.0)
