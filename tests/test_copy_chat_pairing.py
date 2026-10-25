"""Copy Mode is a PAIRING switch, not the content switch.

The dump's own four values (1744884-1744887): Mixed Mode / Separated Mode /
Grouped Mode / Multi-to-one, with "Sources per target: %1" next to them, and two
of them gated: "No permission to use separated mode." /
"No permission to use multi-to-one mode." (1744891-1744892).

This module used ``copy_mode`` for the content switch (copy/rewrite/ai), so the
pairing vocabulary is read from the same key when it names a pairing - an old
task file keeps its old meaning.
"""

from __future__ import annotations

from ogtool.features.copy_chat import (
    GATED_MODES,
    PAIRING_MODES,
    CopyConfig,
    pairing_of,
    sources_for_pairing,
)
from ogtool.sources import SourceMessage


def _messages() -> list[SourceMessage]:
    return [
        SourceMessage(peer="@s1", sender="a", text="one", message_id=1),
        SourceMessage(peer="@s1", sender="a", text="one again", message_id=2),
        SourceMessage(peer="@s2", sender="a", text="two", message_id=3),
        SourceMessage(peer="@s3", sender="a", text="three", message_id=4),
    ]


def test_the_four_modes_are_the_page_s():
    assert PAIRING_MODES == ("mixed", "separated", "grouped", "multi_to_one")
    assert set(GATED_MODES) == {"separated", "multi_to_one"}


def test_a_pairing_name_in_copy_mode_is_read_as_the_pairing():
    assert pairing_of({"copy_mode": "separated"}) == "separated"
    assert pairing_of({"copy_mode": "mixed"}) == "mixed"


def test_the_old_content_mode_is_not_mistaken_for_a_pairing():
    assert pairing_of({"copy_mode": "ai"}) == "mixed"
    assert pairing_of({"copy_mode": "rewrite"}) == "mixed"
    assert pairing_of({}) == "mixed"


def test_a_dash_and_an_underscore_mean_the_same_mode():
    assert pairing_of({"pairing_mode": "multi-to-one"}) == "multi_to_one"


def test_mixed_gives_every_target_every_source():
    config = CopyConfig(pairing="mixed")
    messages = _messages()
    for index in range(3):
        assert sources_for_pairing(config, index, messages, 3) == messages


def test_separated_gives_one_source_per_target():
    config = CopyConfig(pairing="separated")
    messages = _messages()
    assert {m.peer for m in sources_for_pairing(config, 0, messages, 3)} == {"@s1"}
    assert {m.peer for m in sources_for_pairing(config, 1, messages, 3)} == {"@s2"}
    assert {m.peer for m in sources_for_pairing(config, 2, messages, 3)} == {"@s3"}


def test_separated_wraps_when_there_are_fewer_sources_than_targets():
    config = CopyConfig(pairing="separated")
    messages = _messages()
    assert {m.peer for m in sources_for_pairing(config, 3, messages, 5)} == {"@s1"}


def test_multi_to_one_takes_sources_per_target():
    config = CopyConfig(pairing="multi_to_one", sources_per_target=2)
    messages = _messages()
    first = sources_for_pairing(config, 0, messages, 2)
    assert {m.peer for m in first} == {"@s1", "@s2"}
    second = sources_for_pairing(config, 1, messages, 2)
    assert {m.peer for m in second} == {"@s3", "@s1"}


def test_grouped_gives_each_target_a_contiguous_block():
    config = CopyConfig(pairing="grouped")
    messages = _messages()
    blocks = [sources_for_pairing(config, index, messages, 2) for index in range(2)]
    assert [sorted({m.peer for m in block}) for block in blocks] == [
        ["@s1", "@s2"], ["@s3"]]


def test_every_mode_keeps_the_message_ids_of_its_sources():
    config = CopyConfig(pairing="separated")
    messages = _messages()
    picked = sources_for_pairing(config, 0, messages, 3)
    assert [m.message_id for m in picked] == [1, 2]


def test_the_text_cap_wins_over_the_task_cap_for_text():
    """``dailyTextLimitPerAccount`` is a *narrower* knob than ``dailyLimit``."""
    from ogtool.features.copy_chat import effective_limit

    config = CopyConfig(daily_limit=100, daily_text_limit_per_account=5)
    assert effective_limit(config, is_text=True) == 5
    # A media message is not text, so only the task cap applies to it.
    assert effective_limit(config, is_text=False) == 100


def test_a_single_cap_applies_to_both_kinds():
    from ogtool.features.copy_chat import effective_limit

    text_only = CopyConfig(daily_limit=0, daily_text_limit_per_account=7)
    assert effective_limit(text_only, is_text=True) == 7
    assert effective_limit(text_only, is_text=False) == 0


def test_no_cap_means_no_cap():
    from ogtool.features.copy_chat import effective_limit

    assert effective_limit(CopyConfig(), is_text=True) == 0
