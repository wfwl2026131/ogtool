"""#T1 翻译工作台: the reference's right-toolbar translation page.

The reference keeps the workbench outside the console nav, in the right
toolbar next to Unread Messages and QuickReply (:1740461).  Its ten dump
classes map onto this page like this:

- TranslationManager - the quota state machine and the run's orchestration
  (:1737697); its verdicts are verbatim (:1737723-:1737725) and its block
  line is the one a budget refuses with (:1737735).
- TranslationKeyManager - the key check behind "No keys available"
  (:1737694-:1737696) and the key-count warning (:1737744).
- TranslationService / TranslationStrategy / AzureTranslateStrategy - the
  engine call.  ``--strategy azure`` speaks the Azure Translator REST API
  and maps its own error words (``Quota exceeded`` /
  ``Subscription disabled``, :1737778-:1737779); the ai strategy reads
  whatever error the configured endpoint raises.
- TranslationBatchManager - ``--texts-file``, chunked batches and the
  batch cache lookup ``input IN (%1) AND lg_to = ?`` (:1723889).
- TranslationRecord - the ``translation`` table with the dump's own
  primary key ``(input, lg_to)`` (:1723409); a re-translation replaces
  the older row, it does not grow the history.
- TranslationUsageTracker - char counters with a FromCache split and
  periodic reporting (:1737656-:1737681), persisted so a period survives
  the process (:1737734: data preserved for next retry).
- TranslationUsageReport - the report line and the post-report status
  check (:1737718-:1737735).
- TranslatePage - the manual box (``setupManualTranslateSection``,
  :2217721), the received/sent auto switches (:1740808-:1740809) and the
  maintenance banner (:1740824).

There is no team server on this side, so the quota is a local budget
(``--quota-total`` chars per ``--period-days``) and the usage report
lands in the local store; the wording stays the page's, the destination
is honest about being local.
"""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import quote

from ..config import ConfigError, Settings
from ..flags import Flag
from ..runtime import RunContext
from ..sources import SourceMessage
from ..sources import parse_many as parse_sources
from ..targets import Target, parse_many
from ..templates import compose
from . import register
from ._common import (
    accounts_of,
    ai_client,
    close_task,
    header,
    name_of,
    num,
    open_task,
    pause,
    read_list_file,
    record,
    section,
    text,
)

FEATURE = "translate"
SECTION = "translate"
TITLE = "#T1 翻译工作台"

FLAGS = (
    Flag("--text", "texts", str, "text to translate (repeatable)", action="append"),
    Flag(
        "--texts-file",
        "texts_file",
        str,
        "file with one text per line (TranslationBatchManager)",
    ),
    Flag("--lg-from", "lg_from", str, "source language (auto = detect)"),
    Flag("--lg-to", "lg_to", str, "target language"),
    Flag("--strategy", "strategy", str, "engine: ai (configured endpoint) or azure"),
    Flag("--azure-key", "azure_key", str, "Azure Translator subscription key"),
    Flag("--azure-region", "azure_region", str, "Azure Translator region (optional)"),
    Flag(
        "--azure-endpoint",
        "azure_endpoint",
        str,
        "Azure Translator endpoint (default: the public one)",
    ),
    Flag("--target", "target", str, "chat whose history is translated"),
    Flag("--history-limit", "history_limit", int, "messages to read back"),
    Flag(
        "--auto-received",
        "auto_received",
        None,
        "translate received messages (others')",
        action="bool_pair",
    ),
    Flag(
        "--auto-sent",
        "auto_sent",
        None,
        "translate sent messages (your own)",
        action="bool_pair",
    ),
    Flag(
        "--list", "list_only", None, "list the translation history", action="store_true"
    ),
    Flag("--search", "search", str, "history search substring"),
    Flag("--limit", "result_limit", int, "history listing limit"),
    Flag("--quota-total", "quota_total", int, "char budget per period (0 = unlimited)"),
    Flag(
        "--period-days",
        "period_days",
        int,
        "usage period length in days (0 = never rolls over)",
    ),
    Flag(
        "--report-interval", "report_interval", float, "seconds between usage reports"
    ),
    Flag("--batch-size", "batch_size", int, "texts per cache-lookup batch"),
    Flag("--batch-interval", "batch_interval", float, "seconds between engine calls"),
)

#: The history window and the low-quota share are constants, not knobs: the
#: reference hardcodes both shapes and neither ever showed up as a setting.
HISTORY_WINDOW_MINUTES = 120
LOW_QUOTA_PERCENT = 20

ENGINE_TIMEOUT = 20.0
AZURE_ENDPOINT = "https://api.cognitive.microsofttranslator.com"


@dataclass(slots=True)
class TranslateConfig:
    account_labels: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    texts_file: str = ""
    lg_from: str = "auto"
    lg_to: str = "zh"
    strategy: str = "ai"
    azure_key: str = ""
    azure_region: str = ""
    azure_endpoint: str = ""
    target: str = ""
    history_limit: int = 50
    auto_received: bool = False
    auto_sent: bool = False
    list_only: bool = False
    search: str = ""
    result_limit: int = 20
    quota_total: int = 0
    period_days: int = 0
    report_interval: float = 300.0
    batch_size: int = 10
    batch_interval: float = 0.0
    task_name: str = "translate"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> TranslateConfig:
        del settings
        data = section(raw, SECTION)
        strategy = (text(data, "strategy") or "ai").lower()
        if strategy not in ("ai", "azure"):
            raise ConfigError(f"translate: unknown strategy: {strategy}")
        texts = [item for item in data.get("texts") or [] if str(item).strip()]
        texts_file = text(data, "texts_file")
        target = text(data, "target")
        list_only = bool(data.get("list_only", False))
        if not (texts or texts_file or target or list_only):
            raise ConfigError(
                "translate: nothing to do - give --text, "
                "--texts-file, --target or --list"
            )
        return cls(
            account_labels=accounts_of(raw, data),
            texts=[str(item).strip() for item in texts],
            texts_file=texts_file,
            lg_from=text(data, "lg_from", "auto") or "auto",
            lg_to=text(data, "lg_to", "zh") or "zh",
            strategy=strategy,
            azure_key=text(data, "azure_key"),
            azure_region=text(data, "azure_region"),
            azure_endpoint=text(data, "azure_endpoint"),
            target=target,
            history_limit=num(data, "history_limit", 50, kind=int, minimum=1),
            auto_received=bool(data.get("auto_received", False)),
            auto_sent=bool(data.get("auto_sent", False)),
            list_only=list_only,
            search=text(data, "search"),
            result_limit=num(data, "result_limit", 20, kind=int, minimum=1),
            quota_total=num(data, "quota_total", 0, kind=int, minimum=0),
            period_days=num(data, "period_days", 0, kind=int, minimum=0),
            report_interval=num(data, "report_interval", 300.0),
            batch_size=num(data, "batch_size", 10, kind=int, minimum=1),
            batch_interval=num(data, "batch_interval", 0.0),
            task_name=name_of(raw, data, "translate"),
        )


@dataclass(slots=True)
class Item:
    """One text the page would translate, and which side it came from."""

    text: str
    is_out: bool = False


class EngineError(Exception):
    """An engine refusal with the page's own classification."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind  # quota | maintenance | error
        self.detail = detail


def _azure_http(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, str]:
    """The real Azure call; tests pass their own transport instead."""
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=ENGINE_TIMEOUT) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def azure_translate(
    text_value: str,
    lg_from: str,
    lg_to: str,
    *,
    key: str,
    region: str = "",
    endpoint: str = "",
    transport=None,
) -> str:
    """AzureTranslateStrategy: one REST call, the dump's own error words."""
    base = endpoint or AZURE_ENDPOINT
    query = f"/translate?api-version=3.0&to={quote(lg_to)}"
    if lg_from and lg_from != "auto":
        query += f"&from={quote(lg_from)}"
    headers = {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/json"}
    if region:
        headers["Ocp-Apim-Subscription-Region"] = region
    status, payload = (transport or _azure_http)(
        base + query, json.dumps([{"Text": text_value}]).encode("utf-8"), headers
    )
    if status != 200:
        lowered = payload.lower()
        if "quota exceeded" in lowered or status == 429:
            raise EngineError("quota", f"HTTP {status}")
        if "subscription disabled" in lowered:
            raise EngineError("maintenance", f"HTTP {status}")
        raise EngineError("error", f"HTTP {status}: {payload[:80]}")
    try:
        data = json.loads(payload)
        return str(data[0]["translations"][0]["text"])
    except (ValueError, KeyError, IndexError) as error:
        raise EngineError("error", f"unreadable response: {error}") from error


def translate_prompt(ctx: RunContext, config: TranslateConfig, text_value: str) -> str:
    source = "自动检测" if config.lg_from in ("", "auto") else config.lg_from
    return compose(
        "translate",
        settings=ctx.settings,
        lg_from=source,
        lg_to=config.lg_to,
        text=text_value,
    )


def engine(
    config: TranslateConfig, ctx: RunContext, ai: object, text_value: str
) -> tuple[str, str, str]:
    """One text through the engine: ``(output, source, code)``.

    ``code`` is the page's own classification - "" when the engine spoke,
    ``quota`` when its quota ran out, ``maintenance`` for a disabled
    subscription, ``error`` for anything else (the item then falls back).
    """
    if config.strategy == "azure" and config.azure_key:
        try:
            return (
                azure_translate(
                    text_value,
                    config.lg_from,
                    config.lg_to,
                    key=config.azure_key,
                    region=config.azure_region,
                    endpoint=config.azure_endpoint,
                ),
                "azure",
                "",
            )
        except EngineError as error:
            print(f"azure: {error.detail}")
            return text_value, "template", error.kind
    result = ai.generate(  # type: ignore[attr-defined]
        translate_prompt(ctx, config, text_value), fallback=text_value
    )
    if result.ok and result.source == "ai":
        return result.response.strip(), "ai", ""
    lowered = str(result.error).lower()
    if "quota" in lowered:
        return text_value, "template", "quota"
    return text_value, "template", ""


def _chunks(items: list[Item], size: int) -> list[list[Item]]:
    return [items[start : start + size] for start in range(0, len(items), size)]


class UsageTracker:
    """TranslationUsageTracker: char counters that survive the process.

    The store holds one row (``period_start`` and the two counters); a run
    accrues on top of the stored base, reports commit its delta, and a
    failed commit keeps the delta - data preserved for next retry
    (:1737734).  A period older than ``period_days`` resets on load
    (:1737726).
    """

    def __init__(self, store: object, period_days: int) -> None:
        self._store = store
        self.period_days = period_days
        self.run_api = 0
        self.run_cache = 0
        row = store.translation_usage()  # type: ignore[attr-defined]
        base_api = base_cache = 0
        self.period_start = date.today().isoformat()
        if row is not None:
            stored = str(row["period_start"])
            if period_days and self._expired(stored):
                print("Current period usage reset")
                store.reset_translation_usage(  # type: ignore[attr-defined]
                    self.period_start
                )
            else:
                self.period_start = stored
                base_api = int(row["chars_api"])
                base_cache = int(row["chars_cache"])
        self._base_api = base_api
        self._base_cache = base_cache

    def _expired(self, period_start: str) -> bool:
        try:
            start = date.fromisoformat(period_start)
        except ValueError:
            return True
        return (date.today() - start).days >= self.period_days

    def total(self) -> int:
        return self._base_api + self._base_cache + self.run_api + self.run_cache

    def total_api(self) -> int:
        return self._base_api + self.run_api

    def total_cache(self) -> int:
        return self._base_cache + self.run_cache

    def unreported(self) -> int:
        return self.run_api + self.run_cache

    def record(self, text_value: str, from_cache: bool) -> None:
        chars = len(text_value)
        if from_cache:
            self.run_cache += chars
        else:
            self.run_api += chars
        print(
            f"Recorded usage - Length: {chars}, FromCache: {int(from_cache)}"
            f", Current total: {self.total()}, Current API: "
            f"{self.total_api()}, Current cache: {self.total_cache()}"
        )

    def report(self) -> bool:
        """Commit the run's delta; a failed commit preserves it (:1737734)."""
        try:
            self._store.add_translation_usage(  # type: ignore[attr-defined]
                self.period_start, self.run_api, self.run_cache
            )
        except sqlite3.Error:
            return False
        self._base_api += self.run_api
        self._base_cache += self.run_cache
        self.run_api = 0
        self.run_cache = 0
        return True


def quota_state(tracker: UsageTracker, quota_total: int) -> tuple[str, bool, bool]:
    """``(state, can_continue, low)`` - the manager's own state machine."""
    if quota_total <= 0:
        return "sufficient", True, False
    remaining = quota_total - tracker.total()
    if remaining <= 0:
        return "exhausted", False, False
    if remaining <= quota_total * LOW_QUOTA_PERCENT // 100:
        return "low", True, True
    return "sufficient", True, False


def print_verdict(state: str) -> None:
    """The page's three verdicts, verbatim (:1737723-:1737725)."""
    if state == "exhausted":
        print("CRITICAL: Translation quota exhausted! Translation will be blocked.")
    elif state == "low":
        print(
            "WARNING: Translation quota running low. Consider purchasing "
            "additional packages."
        )
    else:
        print("Translation quota is sufficient")


def status_line(tracker: UsageTracker, config: TranslateConfig) -> str:
    """``Package status check completed:`` (:1737750), one budget."""
    _state, can_continue, _low = quota_state(tracker, config.quota_total)
    budget = str(config.quota_total) if config.quota_total else "unlimited"
    return (
        f"Package status check completed: Active packages: 1, "
        f"Has quota: {'yes' if can_continue else 'no'}, "
        f"Total quota: {budget}, Total used: {tracker.total()}"
    )


def after_report_line(tracker: UsageTracker, config: TranslateConfig) -> str:
    """``Package status after usage report:`` (:1737718-:1737721)."""
    state, can_continue, low = quota_state(tracker, config.quota_total)
    remaining = max(0, config.quota_total - tracker.total())
    return (
        f"Package status after usage report: Total remaining: "
        f"{remaining if config.quota_total else 'unlimited'}, "
        f"Overall status: {state}, "
        f"Can continue: {'yes' if can_continue else 'no'}, "
        f"Has low quota: {'yes' if low else 'no'}"
    )


def report_usage(tracker: UsageTracker, config: TranslateConfig) -> None:
    """One usage report cycle (:1737672-:1737684, :1737718-:1737725)."""
    print(
        f"Reporting usage: Total: {tracker.total()}, "
        f"API: {tracker.total_api()}, Cache: {tracker.total_cache()}, "
        f"Period: {tracker.period_start}"
    )
    if not tracker.unreported():
        print("No usage to report, skipping")
        return
    if tracker.report():
        print("Usage reported successfully")
    else:
        print("Usage reporting failed, data preserved for next retry")
    print(after_report_line(tracker, config))
    state, _can, _low = quota_state(tracker, config.quota_total)
    print_verdict(state)


def history_items(config: TranslateConfig, ctx: RunContext, label: str) -> list[Item]:
    """The auto switches' live input: a chat's own recent messages.

    ``Translate received automatically`` keeps what others said, ``Translate
    sent automatically`` keeps our own lines (:1740808-:1740809); the
    ``is_out`` flag rides into the history rows, exactly what the dump's
    translation table stores it for (:1723417).
    """
    target: Target = parse_many([config.target], "custom")[0]
    sender = ctx.sender()
    result = sender.history(
        label,
        target,  # type: ignore[attr-defined]
        minutes=HISTORY_WINDOW_MINUTES,
        limit=config.history_limit,
    )
    if not result.ok:
        print(f"history read failed on {target.display()}: {result.code}")
        return []
    messages: list[SourceMessage] = parse_sources(result.items)
    own = {label}
    items: list[Item] = []
    for message in messages:
        body = message.text.strip()
        if not body:
            continue
        if message.sender in own:
            if config.auto_sent:
                items.append(Item(body, is_out=True))
        elif config.auto_received:
            items.append(Item(body))
    return items


def list_history(config: TranslateConfig, ctx: RunContext) -> int:
    """``--list``: the page's history queries, newest first (:1723885-:1723888).

    A read of the store, not a task: no receipt, the same shape as
    ``accounts list``.
    """
    rows = ctx.store.list_translations(  # type: ignore[attr-defined]
        search=config.search, limit=config.result_limit
    )
    print(f"history: {len(rows)} record(s)")
    for row in rows:
        print(f"{row['input']} -> {row['output']} ({row['lg_from']} -> {row['lg_to']})")
    return 0


def run(config: TranslateConfig, ctx: RunContext) -> int:
    if config.list_only:
        return list_history(config, ctx)

    items = [Item(text_value) for text_value in config.texts]
    if config.texts_file:
        items += [Item(text_value) for text_value in read_list_file(config.texts_file)]
    labels: list[str] = []
    if config.target:
        accounts = ctx.bind(config.account_labels)
        labels = [account.label for account in accounts]
        if not labels:
            raise ConfigError("translate: --target needs an account (--accounts)")
        if ctx.live:
            if config.auto_received or config.auto_sent:
                items += history_items(config, ctx, labels[0])
            else:
                print("history: both auto flags are off, nothing read")
        else:
            print("history: a dry run reads no history")

    header(
        config.task_name,
        strategy=config.strategy,
        lg=f"{config.lg_from} -> {config.lg_to}",
        texts=len(items),
        target=config.target or "-",
        quota=f"{config.quota_total} chars" if config.quota_total else "-",
        batch=str(config.batch_size),
        batch_interval=f"{config.batch_interval:.0f}s"
        if config.batch_interval
        else "-",
        auto=(
            "+".join(
                part
                for part, on in (
                    ("received", config.auto_received),
                    ("sent", config.auto_sent),
                )
                if on
            )
        )
        or "-",
        report=f"every {config.report_interval:.0f}s"
        if config.report_interval
        else "-",
    )

    task = open_task(
        ctx,
        config.task_name,
        FEATURE,
        {"lg_to": config.lg_to, "strategy": config.strategy, "texts": len(items)},
        len(items),
    )
    tracker = UsageTracker(ctx.store, config.period_days)
    ai = ai_client(ctx)
    print("Checking translation package status...")
    print(status_line(tracker, config))
    print_verdict(quota_state(tracker, config.quota_total)[0])
    if config.report_interval > 0:
        print(f"Starting periodic usage reporting every {config.report_interval:.0f}s")
    if not ai.enabled and not (config.strategy == "azure" and config.azure_key):
        print("WARNING: No translation keys available!")
        print("Translation service under maintenance: No available keys")
        print("Translation service is under maintenance. Please try again later.")
        print("No keys available, translation service will use fallback")

    total = len(items)
    ok = failed = cache_hits = 0
    blocked = False
    maintenance = False
    last_report = time.monotonic()
    for start in range(0, total, config.batch_size):
        chunk = items[start : start + config.batch_size]
        cached = ctx.store.translations_for(  # type: ignore[attr-defined]
            [item.text for item in chunk], config.lg_to
        )
        for offset, item in enumerate(chunk):
            index = start + offset + 1
            preview = item.text[:32]
            if blocked:
                failed += 1
                record(ctx, task, "-", item.text[:40], False, "QUOTA_BLOCKED")
                print(
                    f"[{index:03d}/{total:03d}] skipped (quota blocked) src={preview}"
                )
                continue
            if config.quota_total and tracker.total() >= config.quota_total:
                blocked = True
                print(
                    "CRITICAL: Translation quota exhausted! "
                    "Translation will be blocked."
                )
                print("Translation continuation blocked due to quota issues")
                print(f"Quota details - Needed: {len(item.text)}, Remaining: 0")
                failed += 1
                record(ctx, task, "-", item.text[:40], False, "QUOTA_BLOCKED")
                print(
                    f"[{index:03d}/{total:03d}] skipped (quota blocked) src={preview}"
                )
                continue
            hit = cached.get(item.text)
            if hit is not None:
                cache_hits += 1
                ok += 1
                tracker.record(item.text, True)
                record(ctx, task, "-", item.text[:40], True)
                print(
                    f"[{index:03d}/{total:03d}] cache lg_to={config.lg_to} "
                    f"src={preview} -> {hit[:40]}"
                )
                continue
            output, source, code = engine(config, ctx, ai, item.text)
            if code == "quota":
                blocked = True
                print(
                    "CRITICAL: Translation quota exhausted detected in error response"
                )
                failed += 1
                record(ctx, task, "-", item.text[:40], False, "QUOTA_EXCEEDED")
                print(f"[{index:03d}/{total:03d}] failed (quota) src={preview}")
                continue
            if code == "maintenance" and not maintenance:
                maintenance = True
                print(
                    "Translation service is under maintenance. Please try again later."
                )
            if source == "template":
                # The service's own fallback (:1737741): the item keeps its
                # text, nothing is persisted and no usage is counted - a
                # fallback never spends quota it never used.
                ok += 1
                record(ctx, task, "-", item.text[:40], True)
                print(
                    f"[{index:03d}/{total:03d}] ai=template "
                    f"lg_to={config.lg_to} src={preview}"
                )
                continue
            ctx.store.add_translation(  # type: ignore[attr-defined]
                item.text,
                output,
                lg_from=config.lg_from,
                lg_to=config.lg_to,
                is_out=item.is_out,
            )
            tracker.record(item.text, False)
            ok += 1
            record(ctx, task, "-", item.text[:40], True)
            print(
                f"[{index:03d}/{total:03d}] ai={source} "
                f"lg_to={config.lg_to} src={preview} -> {output[:40]}"
            )
            if config.batch_interval:
                pause(ctx, config.batch_interval)
        if (
            config.report_interval > 0
            and time.monotonic() - last_report >= config.report_interval
        ):
            report_usage(tracker, config)
            last_report = time.monotonic()

    if tracker.unreported():
        report_usage(tracker, config)
    elif config.report_interval > 0:
        print("No usage to report, skipping")
    if config.report_interval > 0:
        print("Stopping periodic usage reporting")
    print(f"Processed: {ok + failed} / {total}")
    print(f"ok: {ok}")
    print(f"failed: {failed}")
    print(f"cache hits: {cache_hits}")
    print(f"usage: api {tracker.total_api()} + cache {tracker.total_cache()} chars")
    close_task(ctx, task, ok=ok, failed=failed)
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(TranslateConfig.from_dict(raw, ctx.settings), ctx)


__all__ = [
    "EngineError",
    "Item",
    "TITLE",
    "TranslateConfig",
    "UsageTracker",
    "after_report_line",
    "azure_translate",
    "engine",
    "history_items",
    "list_history",
    "main",
    "print_verdict",
    "quota_state",
    "run",
    "status_line",
]
