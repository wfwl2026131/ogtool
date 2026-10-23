"""#1 复制炒群: the keyword grouping rule and the capture pipeline."""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features.copy_chat import (
    CopyConfig,
    account_groups,
    keyword_groups,
    plan_targets,
    rule_for,
    run,
    select,
)
from ogtool.sources import parse_entry


def config_for(**overrides) -> CopyConfig:
    values = {
        "targets": ["@t1"],
        "sources": ["@src"],
        "messages": [],
    }
    values.update(overrides)
    return CopyConfig(**values)


def test_one_group_when_there_are_no_blank_lines():
    groups = keyword_groups("spam|ads\nbet")
    assert groups == [["spam", "ads", "bet"]]


def test_blank_lines_split_the_rules_into_groups():
    raw = "spam|ads\n\nbet\n\ncrypto"
    assert keyword_groups(raw) == [["spam", "ads"], ["bet"], ["crypto"]]


def test_the_last_group_covers_every_remaining_target():
    groups = [["a"], ["b"]]
    assert [rule_for(groups, index) for index in range(4)] == [
        ["a"], ["b"], ["b"], ["b"],
    ]
    assert rule_for([], 3) == []


def test_a_sender_on_the_block_list_is_dropped_first():
    message = parse_entry("@src|@spammer|hello")
    assert select(message, [], ["@spammer"]) == "sender"
    assert select(message, ["hello"], []) == "keyword"
    assert select(message, ["bye"], []) == ""


def test_each_target_gets_its_own_group_and_accounts():
    messages = [parse_entry("@src|@a|spam one"),
                parse_entry("@src|@b|clean two")]
    config = config_for(targets=["@t1", "@t2"], messages=messages,
                        groups=[["spam"], ["nothing"]],
                        account_groups=[["acc1"], ["acc2"]])
    plans = plan_targets(config, ["acc1", "acc2"])
    assert plans[0].keywords == ["spam"]
    assert [message.text for message in plans[0].collected.kept] == [
        "clean two"]
    assert plans[0].accounts == ["acc1"]
    assert plans[1].keywords == ["nothing"]
    assert [message.text for message in plans[1].collected.kept] == [
        "spam one", "clean two"]
    assert plans[1].accounts == ["acc2"]


def test_replacements_are_applied_before_the_plan():
    messages = [parse_entry("@src|@a|price 100")]
    config = config_for(messages=messages, replacements=[("100", "200")])
    plans = plan_targets(config, ["acc1"])
    assert plans[0].collected.kept[0].text == "price 200"


def test_account_groups_parse_one_line_per_target():
    assert account_groups("a,b\nc") == [["a", "b"], ["c"]]
    assert account_groups(None) == []


def test_from_dict_rejects_more_than_the_source_limit():
    with pytest.raises(ConfigError):
        CopyConfig.from_dict(
            {"copy_chat": {"targets": "@t",
                           "sources": ",".join(f"@s{i}" for i in range(1001))}},
            None,  # type: ignore[arg-type]
        )


def test_run_prints_a_plan_and_a_target_summary(ctx, capsys):
    messages = [parse_entry("@src|@a|keep me"),
                parse_entry("@src|@b|spam here")]
    config = config_for(messages=messages, groups=[["spam"]])
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "captured 1/2" in output
    assert "keyword=1" in output
    assert "PLAN: 1 messages" in output


def test_daily_limit_skips_the_rest(ctx, capsys):
    messages = [parse_entry("@src|@a|one"), parse_entry("@src|@b|two")]
    config = config_for(targets=["@t1", "@t2"], messages=messages,
                        daily_limit=1)
    run(config, ctx)
    output = capsys.readouterr().out
    assert "skipped: no account under the daily limit" in output
    assert "skipped: 2" in output
