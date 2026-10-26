"""#6 auto reply: keyword rules, the duplicate window and the dry-run replay."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ogtool.features.auto_reply import (
    AutoReplyConfig,
    DuplicateCache,
    Rule,
    decide,
    parse_simulated,
    run,
)

START = datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)


def make_config(**section) -> AutoReplyConfig:
    raw = {
        "accounts": ["acc1"],
        "auto_reply": {
            "enabled": True,
            "duplicate_seconds": 60,
            "rules": [
                {"keyword": "天气", "reply": "今天不错"},
                {"keywords": ["价格", "多少钱"], "reply": "稍后私聊"},
            ],
            **section,
        },
        "simulate": [
            "@demo|alice|今天天气不错",
            "@demo|alice|今天天气不错",
            "@demo|bob|多少钱",
            "@demo|carol|毫无关系的闲聊",
        ],
    }
    return AutoReplyConfig.from_dict(raw)


def test_rule_matches_case_insensitively():
    rule = Rule(["Weather"], "sunny")
    assert rule.matches("the WEATHER is fine") == "Weather"
    assert rule.matches("nothing here") == ""


def test_decide_matches_skips_duplicates_and_unknowns():
    config = make_config()
    messages = parse_simulated(config.simulate)
    decisions = decide(config, messages, START)
    reasons = [reason for _, _, reason in decisions]
    assert reasons == ["matched", "duplicate within 60s", "matched",
                       "no rule matched"]
    assert decisions[2][1] is not None
    assert decisions[2][1].reply == "稍后私聊"


def test_duplicate_cache_respects_the_window():
    cache = DuplicateCache(30)
    assert cache.seen("@a", "hi", START) is False
    assert cache.seen("@a", "hi", START + timedelta(seconds=10)) is True
    # The last accepted attempt refreshes the stamp, so this one is inside it.
    assert cache.seen("@a", "hi", START + timedelta(seconds=31)) is True
    # A different chat has its own stamp.
    assert cache.seen("@b", "hi", START + timedelta(seconds=32)) is False
    # After the window has passed a fresh cache entry is allowed again.
    assert cache.seen("@a", "hi", START + timedelta(seconds=120)) is False


def test_duplicate_cache_disabled_by_zero():
    cache = DuplicateCache(0)
    assert cache.seen("@a", "hi", START) is False
    assert cache.seen("@a", "hi", START) is False


def test_disabled_answers_nothing():
    config = make_config(enabled=False)
    decisions = decide(config, parse_simulated(config.simulate), START)
    assert all(rule is None for _, rule, _ in decisions)
    assert {reason for _, _, reason in decisions} == {"disabled"}


def test_parse_simulated_accepts_bare_text():
    messages = parse_simulated(["just text"])
    assert messages[0].peer == "@demo"
    assert messages[0].text == "just text"


def test_dry_run_prints_plan(ctx, capsys):
    config = make_config()
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert output.count("IN  peer=") == 4
    assert "OUT -> rule1" in output
    assert output.count("SKIP: ") == 2
    plan = [line for line in output.splitlines() if line.startswith("PLAN: ")]
    assert plan and plan[0].startswith("PLAN: 2 replies, 1 accounts")
    assert "dry-run: no session was opened" in output


def test_string_rules_are_accepted():
    config = AutoReplyConfig.from_dict(
        {"auto_reply": {"rules": ["hi=>hello"]}}
    )
    assert config.rules[0].keywords == ["hi"]
    assert config.rules[0].reply == "hello"


def test_run_seconds_stops_the_loop_without_a_signal(monkeypatch, ctx,
                                                     capsys):
    """--run-seconds N: the live loop schedules its own stop, the receipt
    closes through the same finally as an interrupt, DONE is printed and
    the exit code is 0 - no external signal needed."""
    import asyncio

    from ogtool.features.auto_reply import AutoReplyConfig, run

    class StopLoop:
        def __init__(self):
            self.scheduled = False

        def call_later(self, delay, callback):
            assert delay == 2
            self.scheduled = True

        def call_soon_threadsafe(self, callback):
            pass

        def run_forever(self):
            assert self.scheduled, "the self-stop was not scheduled"

    monkeypatch.setattr(asyncio, "get_event_loop", lambda: StopLoop())
    # The live branch is where the loop lives: flip the context live with the
    # scripted sender kept in place (same trick as the puppet stagger test).
    ctx.script.codes = ["ok"] * 4
    ctx._sender = ctx.sender()
    ctx.live = True
    config = AutoReplyConfig(rules=[], run_seconds=2)
    code = run(config, ctx)
    assert code == 0
    out = capsys.readouterr().out
    assert "stopping after 2s" in out
    assert "DONE: replies=0" in out
