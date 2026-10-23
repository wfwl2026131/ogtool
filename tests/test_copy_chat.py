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
from ogtool.sources import from_config, parse_entry


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


class HistorySender:
    """A live transport with a canned source history and a send log."""

    def __init__(self, items: list[str], code: str = "ok") -> None:
        self.items = items
        self.code = code
        self.history_calls: list[tuple[str, int, int]] = []
        self.sent: list[tuple[str, str, str]] = []

    def history(self, account, target, minutes=0, limit=0):
        from ogtool.senders import result_for

        self.history_calls.append((target.ref, minutes, limit))
        result = result_for(self.code)
        if result.ok:
            result.items = list(self.items)
            result.count = len(self.items)
        return result

    def send(self, account, target, content):
        from ogtool.senders import SendResult

        self.sent.append((account, target.ref, content.text))
        return SendResult(ok=True, message_id=len(self.sent))

    def close(self) -> None:
        pass


def test_a_live_run_captures_the_source_history(ctx, live, capsys):
    live(HistorySender(["@src|@alice|hello there", "@src|@bob|second one"]))
    config = CopyConfig(targets=["@t1"], sources=["@src"], capture_minutes=15,
                        interval_min=0.0, interval_max=0.0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "source=@src captured 2/2 minutes=15" in output
    assert "captured: 2" in output


def test_the_capture_feeds_the_parser_oldest_first(ctx, live):
    sender = HistorySender(["@src|@alice|first", "@src|@bob|second"])
    live(sender)
    config = CopyConfig(targets=["@t1"], sources=["@src"], capture_minutes=30,
                        interval_min=0.0, interval_max=0.0)
    run(config, ctx)
    assert [text for _, _, text in sender.sent] == ["first", "second"]
    assert sender.history_calls == [("@src", 30, 200)]


def test_a_capture_failure_is_reported_not_swallowed(ctx, live, capsys):
    live(HistorySender([], code="CHANNEL_PRIVATE"))
    config = CopyConfig(targets=["@t1"], sources=["@src"])
    assert run(config, ctx) == 1
    output = capsys.readouterr().out
    assert "capture failed: CHANNEL_PRIVATE" in output
    assert "ERROR: no source history" in output


def test_a_dry_run_never_touches_the_history(ctx, capsys):
    config = CopyConfig(targets=["@t1"], sources=["@src"],
                        messages=from_config({"messages": ["@src|@a|task file"]}))
    assert run(config, ctx) == 0
    assert "source=@src captured" not in capsys.readouterr().out
