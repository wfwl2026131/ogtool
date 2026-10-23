"""#4 AI搬运频道: mapping, filtering, the cap and the rewrite branch."""

from __future__ import annotations

import random

import pytest

from ogtool.ai import AIError
from ogtool.config import ConfigError
from ogtool.features.ai_clone import CloneConfig, parse_mapping, plan, run
from ogtool.sources import parse_entry


def config_for(**overrides) -> CloneConfig:
    values = {"sources": ["@src1"], "targets": ["@dest1"]}
    values.update(overrides)
    return CloneConfig(**values)


def test_mapping_needs_both_sides():
    assert parse_mapping(["@a=@b"]) == {"@a": "@b"}
    with pytest.raises(ConfigError):
        parse_mapping(["@a"])
    with pytest.raises(ConfigError):
        parse_mapping(["=@b"])


def test_plan_honours_the_mapping_and_falls_back_to_the_list():
    messages = [parse_entry("@a|@x|one"), parse_entry("@b|@y|two")]
    config = config_for(sources=["@a", "@b"], targets=["@t1", "@t2"],
                        mapping={"@a": "@special"}, messages=messages)
    items, collected = plan(config, ["acc1"], random.Random(1))
    assert [item.target for item in items] == ["@special", "@t2"]
    assert collected.scanned == 2


def test_keywords_drop_a_post_before_it_is_planned():
    messages = [parse_entry("@src1|@x|buy spam now"),
                parse_entry("@src1|@x|clean post")]
    config = config_for(messages=messages, keywords=["spam"])
    items, collected = plan(config, ["acc1"])
    assert [item.text for item in items] == ["clean post"]
    assert collected.reasons == {"keyword": 1}


def test_replacements_apply_before_the_cap():
    messages = [parse_entry("@src1|@x|year 2025")]
    config = config_for(messages=messages, replacements=[("2025", "2026")])
    items, _ = plan(config, ["acc1"])
    assert items[0].text == "year 2026"


def test_max_items_caps_the_run():
    messages = [parse_entry(f"@src1|@x|post {index}") for index in range(5)]
    config = config_for(messages=messages, max_items=2)
    items, collected = plan(config, ["acc1"])
    assert len(items) == 2
    assert len(collected.kept) == 5


def test_copy_mode_sends_the_text_unchanged(ctx, capsys):
    messages = [parse_entry("@src1|@x|as is")]
    config = config_for(messages=messages)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "copy text=as is" in output
    assert "PLAN: 1 posts" in output


def test_rewrite_mode_uses_the_model(ctx, capsys, fake_ai):
    ctx.ai = fake_ai(lambda prompt, system, config: ("rewritten post", 4))
    messages = [parse_entry("@src1|@x|original")]
    config = config_for(messages=messages, mode="rewrite", style="formal")
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "ai text=rewritten post" in output
    assert "ai calls: 1 (fallback 0)" in output


def test_a_failing_model_falls_back_to_the_original(ctx, capsys, fake_ai):
    def boom(prompt: str, system: str, config):
        raise AIError("AI endpoint unreachable")

    ctx.ai = fake_ai(boom)
    messages = [parse_entry("@src1|@x|original")]
    config = config_for(messages=messages, mode="rewrite")
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "text=original" in output
    assert "fallback 1" in output


class CloneHistorySender:
    """A live transport with a canned channel history and a post log."""

    def __init__(self, items: list[str], code: str = "ok") -> None:
        self.items = items
        self.code = code
        self.history_calls: list[tuple[str, int, int]] = []
        self.sent: list[str] = []

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

        self.sent.append(content.text)
        return SendResult(ok=True, message_id=len(self.sent))

    def close(self) -> None:
        pass


def test_a_live_run_captures_the_source_channel(ctx, live, capsys):
    live(CloneHistorySender(["@src|ch|hello", "@src|ch|world"]))
    config = CloneConfig(sources=["@src"], targets=["@t1"], max_items=5,
                         post_interval_min=0.0, post_interval_max=0.0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "source=@src captured 2/2" in output
    assert "captured: captured 2/2" in output


def test_the_capture_is_read_with_the_carry_limit(ctx, live):
    sender = CloneHistorySender(["@src|ch|one"])
    live(sender)
    config = CloneConfig(sources=["@src"], targets=["@t1"], max_items=7,
                         post_interval_min=0.0, post_interval_max=0.0)
    run(config, ctx)
    assert sender.history_calls == [("@src", 0, 7)]
    assert sender.sent == ["one"]


def test_a_captured_post_still_goes_through_the_filter(ctx, live):
    live(CloneHistorySender(["@src|ch|keep this", "@src|ch|spam here"]))
    config = CloneConfig(sources=["@src"], targets=["@t1"], max_items=5,
                         keywords=["spam"], post_interval_min=0.0,
                         post_interval_max=0.0)
    run(config, ctx)
    sender = ctx._sender
    assert sender.sent == ["keep this"]


def test_a_capture_failure_is_reported_not_swallowed(ctx, live, capsys):
    live(CloneHistorySender([], code="CHANNEL_PRIVATE"))
    config = CloneConfig(sources=["@src"], targets=["@t1"])
    assert run(config, ctx) == 1
    output = capsys.readouterr().out
    assert "capture failed: CHANNEL_PRIVATE" in output
    assert "ERROR: no source history" in output
