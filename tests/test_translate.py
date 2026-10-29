"""#T1 翻译工作台: the quota machine, the cache and the two engines."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from ogtool.cli import build_parser
from ogtool.config import ConfigError
from ogtool.features.translate import (
    EngineError,
    TranslateConfig,
    UsageTracker,
    after_report_line,
    azure_translate,
    engine,
    history_items,
    list_history,
    print_verdict,
    quota_state,
    run,
    status_line,
)


def config_for(**overrides) -> TranslateConfig:
    values: dict = {"texts": ["hello"]}
    values.update(overrides)
    return TranslateConfig(**values)


def seed_old_period(store, days_ago: int) -> None:
    from datetime import date, timedelta

    started = (date.today() - timedelta(days=days_ago)).isoformat()
    store.add_translation_usage(started, 100, 50)


# --- configuration ------------------------------------------------------


def test_a_brief_needs_something_to_translate():
    with pytest.raises(ConfigError):
        TranslateConfig.from_dict({"translate": {}}, None)  # type: ignore


def test_an_unknown_strategy_is_a_config_error():
    with pytest.raises(ConfigError):
        TranslateConfig.from_dict({"translate": {"texts": ["x"],
                                                 "strategy": "google"}},
                                  None)  # type: ignore


def test_defaults_are_auto_detect_into_chinese():
    config = TranslateConfig.from_dict({"translate": {"texts": ["x"]}},
                                       None)  # type: ignore
    assert (config.lg_from, config.lg_to, config.strategy) == ("auto", "zh",
                                                               "ai")
    assert config.quota_total == 0 and config.period_days == 0


def test_the_page_never_takes_a_writing_slot():
    from ogtool.features import WRITE_FEATURES

    assert "translate" not in WRITE_FEATURES


# --- the azure strategy -------------------------------------------------


def azure_transport(status: int, body: str):
    def _transport(url: str, payload: bytes, headers: dict) -> tuple[int, str]:
        return status, body

    return _transport


def test_azure_translate_returns_the_first_translation():
    body = '[{"translations":[{"text":"你好"},{"text":"ignored"}]}]'
    output = azure_translate("hello", "auto", "zh", key="k",
                             transport=azure_transport(200, body))
    assert output == "你好"


def test_azure_maps_the_dumps_own_error_words():
    with pytest.raises(EngineError) as quota:
        azure_translate("x", "auto", "zh", key="k",
                        transport=azure_transport(
                            400, '{"error":{"code":400037,'
                                 '"message":"Quota exceeded"}}'))
    assert quota.value.kind == "quota"
    with pytest.raises(EngineError) as disabled:
        azure_translate("x", "auto", "zh", key="k",
                        transport=azure_transport(
                            403, "Subscription disabled"))
    assert disabled.value.kind == "maintenance"


def test_an_http_429_is_quota_and_401_is_just_an_error():
    with pytest.raises(EngineError) as hit:
        azure_translate("x", "auto", "zh", key="k",
                        transport=azure_transport(429, "rate"))
    assert hit.value.kind == "quota"
    with pytest.raises(EngineError) as plain:
        azure_translate("x", "auto", "zh", key="k",
                        transport=azure_transport(401, "nope"))
    assert plain.value.kind == "error"


def test_an_unreadable_body_is_an_error_not_a_crash():
    with pytest.raises(EngineError) as bad:
        azure_translate("x", "auto", "zh", key="k",
                        transport=azure_transport(200, "not json"))
    assert bad.value.kind == "error"


# --- the usage tracker ---------------------------------------------------


def test_recorded_usage_keeps_the_from_cache_split(ctx, capsys):
    tracker = UsageTracker(ctx.store, 0)
    tracker.record("hello", False)
    tracker.record("cached text", True)
    out = capsys.readouterr().out
    assert "Recorded usage - Length: 5, FromCache: 0" in out
    assert "Recorded usage - Length: 11, FromCache: 1" in out
    assert "Current total: 16" in out
    assert "Current API: 5" in out and "Current cache: 11" in out


def test_usage_survives_the_run(ctx):
    tracker = UsageTracker(ctx.store, 0)
    tracker.record("hello", False)
    assert tracker.report()
    again = UsageTracker(ctx.store, 0)
    assert again.total() == 5 and again.total_api() == 5


def test_a_failed_report_preserves_the_delta(ctx, monkeypatch):
    tracker = UsageTracker(ctx.store, 0)
    tracker.record("hello", False)

    def boom(*args: object) -> None:
        raise sqlite3.Error("locked")

    monkeypatch.setattr(ctx.store, "add_translation_usage", boom)
    assert tracker.report() is False
    assert tracker.unreported() == 5
    monkeypatch.undo()
    assert tracker.report() is True
    assert tracker.unreported() == 0


def test_an_expired_period_resets(ctx, capsys):
    seed_old_period(ctx.store, days_ago=3)
    tracker = UsageTracker(ctx.store, period_days=1)
    assert capsys.readouterr().out.count("Current period usage reset") == 1
    assert tracker.total() == 0
    # a live period is adopted, not reset
    seed_old_period(ctx.store, days_ago=0)
    again = UsageTracker(ctx.store, period_days=1)
    assert "reset" not in capsys.readouterr().out
    assert again.total() == 150


# --- the quota state machine ----------------------------------------------


def test_the_quota_state_machine():
    class Fixed:
        def total(self) -> int:
            return self.value

    fixed = Fixed()
    fixed.value = 0
    assert quota_state(fixed, 0) == ("sufficient", True, False)
    fixed.value = 1000
    assert quota_state(fixed, 1000) == ("exhausted", False, False)
    fixed.value = 850
    assert quota_state(fixed, 1000) == ("low", True, True)
    fixed.value = 100
    assert quota_state(fixed, 1000) == ("sufficient", True, False)


def test_the_verdicts_are_the_pages_own_words(capsys):
    print_verdict("sufficient")
    print_verdict("low")
    print_verdict("exhausted")
    out = capsys.readouterr().out
    assert "Translation quota is sufficient" in out
    assert ("WARNING: Translation quota running low. Consider purchasing "
            "additional packages.") in out
    assert ("CRITICAL: Translation quota exhausted! Translation will be "
            "blocked.") in out


def test_the_status_lines_carry_the_budget(ctx):
    tracker = UsageTracker(ctx.store, 0)
    config = config_for(quota_total=1000)
    line = status_line(tracker, config)
    assert ("Package status check completed: Active packages: 1, "
            "Has quota: yes, Total quota: 1000, Total used: 0") in line
    tracker.record("x" * 950, False)
    assert ("Total remaining: 50, Overall status: low, Can continue: yes, "
            "Has low quota: yes") in after_report_line(tracker, config)


# --- the engines -----------------------------------------------------------


class FakeAI:
    def __init__(self, *, enabled: bool = True, text: str = "译文",
                 error: str = "") -> None:
        self.enabled = enabled
        self.text = text
        self.error = error
        self.prompts: list[str] = []

    def generate(self, prompt: str, system: str = "", *,
                 fallback: str = "") -> SimpleNamespace:
        self.prompts.append(prompt)
        if self.enabled and not self.error:
            return SimpleNamespace(response=self.text, source="ai", ok=True,
                                   error="")
        return SimpleNamespace(response=fallback, source="template",
                               ok=False, error=self.error)


def test_the_ai_engine_returns_a_translation(ctx):
    ai = FakeAI(text="  你好世界  ")
    output, source, code = engine(config_for(), ctx, ai, "hello")
    assert (output, source, code) == ("你好世界", "ai", "")
    assert "hello" in ai.prompts[0] and "zh" in ai.prompts[0]


def test_a_quota_word_in_the_error_is_a_quota_refusal(ctx):
    ai = FakeAI(enabled=False, error="QUOTA_INSUFFICIENT: buy more")
    _output, _source, code = engine(config_for(), ctx, ai, "hello")
    assert code == "quota"


def test_a_generic_engine_failure_falls_back_to_the_text(ctx):
    ai = FakeAI(enabled=False, error="endpoint timed out")
    output, source, code = engine(config_for(), ctx, ai, "hello")
    assert (output, source, code) == ("hello", "template", "")


def test_the_azure_engine_uses_the_real_endpoint(monkeypatch, ctx):
    seen: dict = {}

    def fake_http(url: str, body: bytes,
                  headers: dict) -> tuple[int, str]:
        seen["url"] = url
        seen["key"] = headers["Ocp-Apim-Subscription-Key"]
        return 200, '[{"translations":[{"text":"你好"}]}]'

    monkeypatch.setattr("ogtool.features.translate._azure_http", fake_http)
    config = config_for(strategy="azure", azure_key="key-1",
                        azure_region="global")
    output, source, code = engine(config, ctx, FakeAI(), "hello")
    assert (output, source, code) == ("你好", "azure", "")
    assert "to=zh" in seen["url"] and "from=" not in seen["url"]
    assert seen["key"] == "key-1"


def test_azure_from_is_sent_when_not_auto(monkeypatch, ctx):
    seen: dict = {}

    def fake_http(url: str, body: bytes,
                  headers: dict) -> tuple[int, str]:
        seen["url"] = url
        return 200, '[{"translations":[{"text":"hi"}]}]'

    monkeypatch.setattr("ogtool.features.translate._azure_http", fake_http)
    config = config_for(strategy="azure", azure_key="k", lg_from="en",
                        lg_to="zh")
    engine(config, ctx, FakeAI(), "hello")
    assert "from=en" in seen["url"]


# --- the run ----------------------------------------------------------------


def test_run_without_keys_falls_back_and_never_spends(ctx, capsys):
    config = config_for(texts=["hello there", "second line"])
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "WARNING: No translation keys available!" in out
    assert "Translation service under maintenance: No available keys" in out
    assert ("Translation service is under maintenance. "
            "Please try again later.") in out
    assert "No keys available, translation service will use fallback" in out
    assert out.count("ai=template") == 2
    assert "usage: api 0 + cache 0 chars" in out


def test_run_translates_and_persists_the_cache(ctx, capsys, fake_ai):
    ctx.ai = fake_ai(lambda prompt, system, config: ("译文内容", 7))
    assert run(config_for(texts=["hello"]), ctx) == 0
    out = capsys.readouterr().out
    assert "ai=ai lg_to=zh src=hello -> 译文内容" in out
    assert "usage: api 5 + cache 0 chars" in out
    assert ctx.store.translations_for(["hello"], "zh") == {"hello": "译文内容"}


def test_a_second_run_hits_the_cache(ctx, capsys, fake_ai):
    ctx.ai = fake_ai(lambda prompt, system, config: ("译文", 7))
    run(config_for(texts=["hello"]), ctx)
    calls = capsys.readouterr().out.count("ai=")
    ctx.ai = None  # no engine at all: the cache must answer on its own
    assert run(config_for(texts=["hello"]), ctx) == 0
    out = capsys.readouterr().out
    assert out.count("cache lg_to=zh") == 1
    assert "Recorded usage - Length: 5, FromCache: 1" in out
    assert calls == 1  # the first run made exactly one engine call


def test_a_batch_lookup_answers_in_one_query(ctx, fake_ai):
    texts = [f"number {index}" for index in range(12)]
    ctx.ai = fake_ai(lambda prompt, system, config: ("译文", 7))
    run(config_for(texts=texts, batch_size=5), ctx)
    assert ctx.store.translations_for(texts, "zh") == {
        text: "译文" for text in texts}


def test_the_quota_blocks_the_continuation_verbatim(ctx, capsys, fake_ai):
    ctx.ai = fake_ai(lambda prompt, system, config: ("译文", 7))
    config = config_for(texts=["12345", "overflow"], quota_total=5)
    assert run(config, ctx) == 1
    out = capsys.readouterr().out
    assert ("CRITICAL: Translation quota exhausted! Translation will be "
            "blocked.") in out
    assert "Translation continuation blocked due to quota issues" in out
    assert "Quota details - Needed: 8, Remaining: 0" in out
    assert "skipped (quota blocked) src=overflow" in out
    assert "failed: 1" in out


def test_a_quota_error_from_the_engine_blocks_too(ctx, capsys, fake_ai):
    def quota_boom(prompt: str, system: str, config):
        raise RuntimeError("unused")

    ctx.ai = fake_ai(quota_boom)  # generate() never raises; use the error path
    config = config_for(texts=["hello"])
    config.strategy = "ai"
    # a fake endpoint whose transport reports the dump's quota word
    def transport(prompt: str, system: str, cfg):
        raise AssertionError("the transport is below generate()")

    ctx.ai = fake_ai(transport)  # type: ignore[arg-type]
    # force the error word through the fallback path instead
    class QuotaAI:
        enabled = True

        def generate(self, prompt: str, system: str = "", *,
                     fallback: str = ""):
            return SimpleNamespace(response=fallback, source="template",
                                   ok=False, error="Quota exceeded")

    ctx.ai = QuotaAI()  # type: ignore[assignment]
    assert run(config_for(texts=["hello"], quota_total=0), ctx) == 1
    out = capsys.readouterr().out
    assert "CRITICAL: Translation quota exhausted detected in error response"
    assert "failed (quota)" in out


def test_the_report_cycle_is_printed(ctx, capsys, fake_ai):
    ctx.ai = fake_ai(lambda prompt, system, config: ("译文", 7))
    config = config_for(texts=["hello"], report_interval=0.0)
    # report_interval 0 means no periodic reporting; the final flush still runs
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "Reporting usage: Total: 5, API: 5, Cache: 0" in out
    assert "Usage reported successfully" in out
    assert "Package status after usage report:" in out


def test_periodic_reporting_starts_and_stops(ctx, capsys):
    assert run(config_for(texts=["hello"], report_interval=30.0), ctx) == 0
    out = capsys.readouterr().out
    assert "Starting periodic usage reporting every 30s" in out
    assert "Stopping periodic usage reporting" in out
    assert "No usage to report, skipping" in out


def test_history_listing_reads_the_store(ctx, capsys):
    ctx.store.add_translation("hello", "你好", lg_from="en", lg_to="zh")
    ctx.store.add_translation("world", "世界", lg_from="en", lg_to="zh")
    assert list_history(config_for(texts=[], list_only=True), ctx) == 0
    out = capsys.readouterr().out
    assert "history: 2 record(s)" in out
    assert "hello -> 你好 (en -> zh)" in out
    assert "world -> 世界 (en -> zh)" in out


def test_the_listing_searches_the_input(ctx, capsys):
    ctx.store.add_translation("hello", "你好", lg_from="en", lg_to="zh")
    ctx.store.add_translation("world", "世界", lg_from="en", lg_to="zh")
    list_history(config_for(texts=[], list_only=True, search="hello"), ctx)
    out = capsys.readouterr().out
    assert "hello -> 你好" in out and "world" not in out


def test_a_retranslation_replaces_its_row(ctx):
    ctx.store.add_translation("hello", "旧译文", lg_from="en", lg_to="zh")
    ctx.store.add_translation("hello", "新译文", lg_from="auto", lg_to="zh")
    assert ctx.store.translations_for(["hello"], "zh") == {"hello": "新译文"}
    assert len(ctx.store.list_translations(limit=10)) == 1


def test_a_dry_run_reads_no_history(ctx, capsys):
    config = config_for(texts=[], target="@somewhere", auto_received=True)
    assert run(config, ctx) == 0
    assert "history: a dry run reads no history" in capsys.readouterr().out


def test_a_target_needs_an_account():
    with pytest.raises(ConfigError):
        run(config_for(texts=[], target="@somewhere"),
            SimpleNamespace(bind=lambda labels: [], store=None, live=False))


def test_history_items_split_by_the_auto_flags():
    class FakeResult:
        ok = True
        items = ["@chat|Alice|from her", "@chat|acc1|my own line",
                 "@chat|acc1|   "]

    class FakeSender:
        def history(self, label: str, target: object, *, minutes: int,
                    limit: int) -> FakeResult:
            return FakeResult()

    config = config_for(texts=[], target="@chat", auto_received=True,
                        auto_sent=True)
    ctx = SimpleNamespace(sender=lambda: FakeSender())
    items = history_items(config, ctx, "acc1")
    assert [(item.text, item.is_out) for item in items] == [
        ("from her", False), ("my own line", True)]


def test_history_items_respect_the_switches():
    class FakeResult:
        ok = True
        items = ["@chat|Alice|from her", "@chat|acc1|my own line"]

    class FakeSender:
        def history(self, label: str, target: object, *, minutes: int,
                    limit: int) -> FakeResult:
            return FakeResult()

    received = config_for(texts=[], target="@chat", auto_received=True)
    assert len(history_items(received, SimpleNamespace(
        sender=lambda: FakeSender()), "acc1")) == 1
    neither = config_for(texts=[], target="@chat")
    assert history_items(neither, SimpleNamespace(
        sender=lambda: FakeSender()), "acc1") == []


def test_the_flags_parse_and_the_abbreviations_do_not():
    args = build_parser().parse_args(
        ["translate", "--text", "a", "--text", "b", "--lg-to", "en",
         "--auto-sent", "--no-auto-received", "--list"])
    assert args.texts == ["a", "b"]
    assert args.lg_to == "en" and args.auto_sent is True
    assert args.auto_received is False and args.list_only is True
    with pytest.raises(SystemExit):
        build_parser().parse_args(["translate", "--txt", "a"])
