"""#1 copy chat - replay what the source groups said into the target groups.

The capture step reads a chat, the filter step drops what the keyword rules
exclude, the rewrite step applies ``left=right`` replacements, and the schedule
step spreads the survivors over the target groups with one account list per
target.

The keyword box has a grouping rule the page states in its own hint: blank
lines split the rules into groups, the first group applies to the first target,
the second to the second, and the last group covers every remaining target.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..ai import AIClient
from ..config import ConfigError, Settings
from ..flags import Flag
from ..limiter import format_duration
from ..report import dedupe
from ..runtime import RunContext
from ..senders import Content
from ..sources import (
    Collected,
    SourceMessage,
    by_peer,
    from_config,
    keep,
    parse_replacements,
    replace_keywords,
)
from ..sources import parse_many as parse_source_messages
from ..targets import Target, parse_many
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    name_of,
    num,
    open_task,
    pause,
    record,
    section,
    targets_of,
    text,
)

FEATURE = "copy_chat"
SECTION = "copy_chat"
TITLE = "#1 复制炒群"

#: The page refuses a longer source list (``Sources: 0/1000``).
SOURCE_LIMIT = 1000

#: How many messages one live capture reads from each source chat.
CAPTURE_LIMIT = 200

FLAGS = (
    Flag("--targets", "targets", str, "targets, comma separated"),
    Flag("--targets-file", "files", str, "file with one target per line",
         action="append"),
    Flag("--sources", "sources", str, "source groups, comma separated"),
    Flag("--sources-file", "source_files", str,
         "file with one source per line", action="append"),
    Flag("--copy-mode", "copy_mode", str, "copy, rewrite or ai"),
    Flag("--capture-minutes", "capture_minutes", int,
         "how far back the capture goes"),
    Flag("--daily-limit", "daily_limit", int,
         "text messages one account may send per day"),
    Flag("--multiplier", "multiplier", int, "message multiplier"),
    Flag("--polish", "polish", int, "polish level 0-3"),
    Flag("--ai-polish-level", "ai_polish_level", int,
         "aiPolishLevel: how hard the rewrite is (0-3)"),
    Flag("--message-speed", "message_speed", int,
         "messageSpeed: sent messages per minute, 0 = as fast as allowed"),
    Flag("--lightweight", "lightweight_mode", None,
         "lightweightMode: skip keyword replacement and media rewrite",
         action="store_true"),
    Flag("--ignore-bot", "ignore_bot_messages", None,
         "drop messages captured from bots", action="store_true"),
    Flag("--ignore-at", "ignore_at_messages", None,
         "drop messages that mention someone", action="store_true"),
    Flag("--ignore-tme", "ignore_tme_links", None,
         "drop messages that carry a t.me link", action="store_true"),
    Flag("--ignore-pinned", "ignore_pinned_messages", None,
         "drop pinned messages", action="store_true"),
    Flag("--ignore-admin", "ignore_admin_messages", None,
         "drop messages posted by an admin", action="store_true"),
    Flag("--interval-min", "interval_min", float, "minimum send interval"),
    Flag("--interval-max", "interval_max", float, "maximum send interval"),
    Flag("--filter-keywords", "filter_keywords", str,
         "keywords, one per line; | separates alternatives; a blank line "
         "starts the rule set of the next target"),
    Flag("--filter-senders", "filter_senders", str,
         "sender ids to drop, comma separated"),
    Flag("--replacements", "replacements", str,
         "left=right rewrites, one per line"),
    Flag("--account-groups", "account_groups", str,
         "one account list per target, comma separated, one line per target"),
    Flag("--message", "messages", str, "captured source message, "
         "peer|sender|text (repeatable)", action="append"),
)


def keyword_groups(raw: object) -> list[list[str]]:
    """Rules as groups: blank line starts a new group, ``|`` splits a line."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        lines = ["" if item is None else str(item) for item in raw]
    else:
        lines = str(raw).splitlines()

    groups: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if not line.strip():
            if current:
                groups.append(current)
                current = []
            continue
        current.extend(part.strip() for part in line.split("|")
                       if part.strip())
    if current:
        groups.append(current)
    return [dedupe(group) for group in groups if group]


def rule_for(groups: list[list[str]], target_index: int) -> list[str]:
    """The keywords a target uses: its own group, else the last one."""
    if not groups:
        return []
    return groups[min(target_index, len(groups) - 1)]


def account_groups(raw: object) -> list[list[str]]:
    """One account list per target: ``acc1,acc2`` on each line."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        lines = [str(item) for item in raw]
    else:
        lines = str(raw).splitlines()
    out: list[list[str]] = []
    for line in lines:
        if not line.strip():
            continue
        out.append([part.strip() for part in line.replace("|", ",").split(",")
                    if part.strip()])
    return out


#: The page's five ignore switches, in the order it lists them.
IGNORE_SENDERS = (
    "ignore_bot_messages",
    "ignore_at_messages",
    "ignore_tme_links",
    "ignore_pinned_messages",
    "ignore_admin_messages",
)


def ignore_reason(message: SourceMessage, ignores: dict[str, bool]) -> str:
    """Why one message is dropped by the ignore switches, ``""`` when it is not.

    Three of the five can be decided from the text itself; the other two need
    knowledge only the capture has, so they read the ``flags`` the capture put
    on the message and never guess (a message with no flags is not dropped on
    a ground nobody observed).
    """
    if ignores.get("ignore_bot_messages") and "bot" in message.flags:
        return "bot"
    if ignores.get("ignore_at_messages") and "@" in message.text:
        return "at"
    if ignores.get("ignore_tme_links") and "t.me/" in message.text:
        return "tme"
    if ignores.get("ignore_pinned_messages") and "pinned" in message.flags:
        return "pinned"
    if ignores.get("ignore_admin_messages") and "admin" in message.flags:
        return "admin"
    return ""


def select(message: SourceMessage, keywords: list[str],
           senders: list[str], ignores: dict[str, bool] | None = None) -> str:
    """``""`` when the message survives, else the reason it is dropped."""
    if senders and message.sender in senders:
        return "sender"
    reason = ignore_reason(message, ignores or {})
    if reason:
        return reason
    if not keep(message, keywords):
        return "keyword"
    return ""


@dataclass(slots=True)
class CopyConfig:
    account_labels: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    copy_mode: str = "copy"
    #: ``Copy Mode`` in the page's pairing sense (see PAIRING_MODES).
    pairing: str = "mixed"
    capture_minutes: int = 60
    daily_limit: int = 0
    multiplier: int = 1
    polish: int = 0
    interval_min: float = 5.0
    interval_max: float = 12.0
    groups: list[list[str]] = field(default_factory=list)
    senders: list[str] = field(default_factory=list)
    replacements: list[tuple[str, str]] = field(default_factory=list)
    account_groups: list[list[str]] = field(default_factory=list)
    messages: list[SourceMessage] = field(default_factory=list)
    ignores: dict[str, bool] = field(default_factory=dict)
    #: ``keywordReplacementGroups``: per-target replacement sets, the same way
    #: ``filterKeywordGroups`` gives per-target keywords.
    replacement_groups: list[list[tuple[str, str]]] = field(
        default_factory=list)
    #: ``sourcesPerTarget``: how many sources feed one target in multi-to-one.
    sources_per_target: int = 0
    #: ``dailyTextLimitPerAccount``: a per-account cap under the task cap.
    daily_text_limit_per_account: int = 0
    #: ``collectionIntervalMinutes``: how often the collector polls.
    collection_interval_minutes: int = 0
    #: ``crossoverMessageId``: passive mode starts after this message.
    crossover_message_id: int = 0
    ai_polish_level: int = 0
    #: ``aiPolishBatchSize``: messages per polish call.
    ai_polish_batch_size: int = 10
    message_speed: int = 0
    lightweight_mode: bool = False
    task_name: str = "copy-chat"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> CopyConfig:
        del settings
        data = section(raw, SECTION)
        targets = [target.ref for target in targets_of(data)]
        if not targets:
            raise ConfigError(f"{SECTION}: no target given (--targets)")

        sources = [target.ref for target in targets_of(data, "sources",
                                                       kind="group")]
        if len(sources) > SOURCE_LIMIT:
            raise ConfigError(
                f"{SECTION}: {len(sources)} sources, the page allows "
                f"{SOURCE_LIMIT}"
            )

        return cls(
            account_labels=accounts_of(raw, data),
            targets=targets,
            sources=sources,
            copy_mode=text(data, "copy_mode", "copy"),
            pairing=pairing_of(data),
            capture_minutes=num(data, "capture_minutes", 60, kind=int,
                                minimum=0),
            daily_limit=num(data, "daily_limit", 0, kind=int, minimum=0),
            multiplier=num(data, "multiplier", 1, kind=int, minimum=1),
            polish=num(data, "polish", 0, kind=int, minimum=0),
            interval_min=num(
                data, "min_speech_interval_seconds",
                num(data, "interval_min", 5.0)),
            interval_max=num(
                data, "max_speech_interval_seconds",
                num(data, "interval_max", 12.0)),
            replacement_groups=replacement_groups(
                data.get("keyword_replacement_groups")),
            groups=keyword_groups(data.get("filter_keywords")),
            senders=_split(data.get("filter_senders")),
            replacements=parse_replacements(_lines(data.get("replacements"))),
            account_groups=account_groups(data.get("account_groups")),
            messages=from_config(data),
            ignores=read_ignores(data),
            ai_polish_level=num(data, "ai_polish_level", 0, kind=int,
                                minimum=0),
            ai_polish_batch_size=num(data, "ai_polish_batch_size", 10,
                                     kind=int, minimum=1),
            sources_per_target=num(data, "sources_per_target", 0, kind=int,
                                   minimum=0),
            daily_text_limit_per_account=num(
                data, "daily_text_limit_per_account", 0, kind=int, minimum=0),
            collection_interval_minutes=num(
                data, "collection_interval_minutes", 0, kind=int, minimum=0),
            crossover_message_id=num(data, "crossover_message_id", 0, kind=int,
                                     minimum=0),
            message_speed=num(data, "message_speed", 0, kind=int, minimum=0),
            lightweight_mode=bool(data.get("lightweight_mode")),
            task_name=name_of(raw, data, "copy-chat"),
        )


def replacement_groups(value: object) -> list[list[tuple[str, str]]]:
    """``keywordReplacementGroups``: one replacement list per target index."""
    if not value:
        return []
    groups: list[list[tuple[str, str]]] = []
    for block in value if isinstance(value, (list, tuple)) else [value]:
        if isinstance(block, str):
            groups.append(parse_replacements(block.splitlines()))
        else:
            groups.append(parse_replacements([str(item) for item in block]))
    return groups


def _lines(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return str(value).splitlines()


#: What each ``aiPolishLevel`` asks for. Level 0 never calls the endpoint.
POLISH_PROMPTS = {
    1: "只做最小润色：改错字、补标点，不改变句子结构和长度。",
    2: "中等改写：保持原意，换掉重复的词，让句子更顺，长度接近原文。",
    3: "彻底改写：保持原意与语气，重写句式和用词，可以明显不同于原文。",
}


def polish_level(config: CopyConfig) -> int:
    """The level that actually applies: ``aiPolishLevel`` first, then ``polish``."""
    return config.ai_polish_level or config.polish


def polish_text(ai: object, text: str, level: int) -> tuple[str, str]:
    """Rewrite one message at *level*; returns ``(text, source)``.

    A level outside the table, or an endpoint that is not there, leaves the
    text alone and says so: a polish that silently changes nothing looks the
    same as one that ran and liked the original.
    """
    if level not in POLISH_PROMPTS or not text.strip():
        return text, "none"
    prompt = (f"{POLISH_PROMPTS[level]}\n\n原文：\n{text}\n\n"
              f"只输出改写后的正文，不要解释。")
    generated = ai.generate(prompt, fallback=text)  # type: ignore[attr-defined]
    answer = (generated.response or "").strip()
    if answer and generated.source == "ai":
        return answer, "ai"
    return text, generated.source or "template"


def read_ignores(data: dict) -> dict[str, bool]:
    """The five switches, as they sit in the page's config."""
    return {name: bool(data.get(name)) for name in IGNORE_SENDERS}


def parse_batch(answer: str, count: int) -> list[str | None]:
    """Numbered answer -> one entry per input line, ``None`` when missing.

    The page logs "AI polish batch parse empty", so a batch that comes back in
    the wrong shape is a known outcome: the missing lines keep their original
    text instead of shifting the rest by one.
    """
    found: dict[int, str] = {}
    for line in answer.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        head, _, rest = stripped.partition(".")
        if head.isdigit() and rest.strip():
            index = int(head) - 1
            if 0 <= index < count and index not in found:
                found[index] = rest.strip()
    return [found.get(index) for index in range(count)]


def polish_batch(ai: object, texts: list[str], level: int,
                 batch_size: int = 10) -> tuple[list[str], int, int]:
    """Rewrite *texts* in batches; returns (texts, applied, failed_batches)."""
    out = list(texts)
    applied = 0
    failed = 0
    size = max(1, batch_size)
    for start in range(0, len(texts), size):
        chunk = texts[start:start + size]
        if level not in POLISH_PROMPTS:
            break
        listing = "\n".join(f"{index}. {text}" for index, text in
                             enumerate(chunk, start=1))
        prompt = (f"{POLISH_PROMPTS[level]}\n\n待改写（按编号逐条返回，"
                  f"每条一行，格式「编号. 正文」）：\n{listing}")
        generated = ai.generate(prompt, fallback="")  # type: ignore[attr-defined]
        if generated.source != "ai":
            failed += 1
            continue
        answers = parse_batch(generated.response or "", len(chunk))
        if not any(answers):
            failed += 1
            print(f"ai polish batch empty at {start + 1}.."
                  f"{start + len(chunk)}/{len(texts)}")
            continue
        for offset, answer in enumerate(answers):
            if answer:
                out[start + offset] = answer
                applied += 1
    return out, applied, failed


def polish_plan(plans: list[TargetPlan], ai: object, level: int,
                batch_size: int = 10) -> int:
    """Rewrite the kept messages of every plan; returns how many changed.

    The plans share one capture, so a message is rewritten once and then sent
    to every target with the same wording — polishing per target would give
    the same source message two different texts for no reason.
    """
    unique: dict[int, str] = {}
    order: list[int] = []
    for plan in plans:
        for message in plan.collected.kept:
            key = message.message_id
            if key not in unique:
                unique[key] = message.text
                order.append(key)
    if not order:
        return 0
    originals = [unique[key] for key in order]
    polished, changed, failed = polish_batch(ai, originals, level, batch_size)
    if failed:
        print(f"ai polish: {failed} batch(es) kept their original text")
    for key, rewritten in zip(order, polished, strict=True):
        unique[key] = rewritten
    for plan in plans:
        for message in plan.collected.kept:
            message.text = unique[message.message_id]
    return changed


def _split(value: object) -> list[str]:
    return [part.strip() for part in ",".join(_lines(value)).split(",")
            if part.strip()]


#: The page's Copy Mode: how sources are paired with targets. Mixed (every
#: source feeds every target) is the default; the other three narrow that
#: pairing, and two of them are gated behind a permission on the reference -
#: "No permission to use separated mode." / "No permission to use multi-to-one
#: mode." (1744891-1744892).
PAIRING_MODES = ("mixed", "separated", "grouped", "multi_to_one")
GATED_MODES = {
    "separated": "No permission to use separated mode.",
    "multi_to_one": "No permission to use multi-to-one mode.",
}


def pairing_of(data: dict) -> str:
    """The pairing mode, read from ``copy_mode`` when it names one.

    The reference spells the pairing switch ``Copy Mode`` with those four
    values, while this module used ``copy_mode`` for the content switch
    (copy / rewrite / ai). Reading the new vocabulary here keeps an old task
    file - and the old meanings - working.
    """
    for key in ("pairing_mode", "copy_mode"):
        value = str(data.get(key) or "").strip().lower().replace("-", "_")
        if value in PAIRING_MODES:
            return value
    return "mixed"


@dataclass(slots=True)
class TargetPlan:
    target: str
    keywords: list[str]
    collected: Collected
    accounts: list[str]


def sources_for_target(messages: list[SourceMessage], sources: list[str],
                       per_target: int) -> list[SourceMessage]:
    """``sourcesPerTarget``: split the capture so each target sees its slice.

    The page's multi-to-one mode gives a target a fixed number of sources
    instead of the whole list, which is how one target keeps a narrow theme
    while another target of the same task copies something else.
    """
    if per_target <= 0 or not sources:
        return messages
    chunks = [sources[index:index + per_target]
              for index in range(0, len(sources), per_target)]
    return chunks


def sources_for_pairing(config: CopyConfig, index: int,
                        all_sources: list[SourceMessage],
                        target_count: int) -> list[SourceMessage]:
    """The sources one target sees, under the page's Copy Mode.

    - mixed: every source, which is the old behaviour and stays the default;
    - separated: one source per target, in order, wrapping when there are fewer
      sources than targets;
    - grouped: the sources split into ``target_count`` contiguous blocks, so a
      target gets a coherent slice instead of a single chat;
    - multi_to_one: ``sourcesPerTarget`` sources per target, also in order.
    """
    mode = config.pairing
    if mode == "mixed" or not all_sources or target_count <= 0:
        return all_sources
    peers: list[str] = []
    for message in all_sources:
        if message.peer not in peers:
            peers.append(message.peer)
    if not peers:
        return all_sources
    if mode == "separated":
        chosen = [peers[index % len(peers)]]
    elif mode == "multi_to_one":
        per_target = max(1, config.sources_per_target or 1)
        start = (index * per_target) % len(peers)
        chosen = [peers[(start + step) % len(peers)]
                  for step in range(min(per_target, len(peers)))]
    else:  # grouped
        blocks = max(1, min(target_count, len(peers)))
        size = max(1, -(-len(peers) // blocks))
        block = index % blocks
        chosen = peers[block * size:(block + 1) * size]
    allowed = set(chosen)
    return [message for message in all_sources if message.peer in allowed]


def plan_targets(config: CopyConfig, labels: list[str]) -> list[TargetPlan]:
    """One plan per target: its rule group, its survivors, its accounts."""
    all_sources = by_peer(config.messages, config.sources)
    if config.crossover_message_id:
        # Passive mode starts after the crossover: everything up to and
        # including it was already handled by the previous run.
        all_sources = [message for message in all_sources
                       if message.message_id > config.crossover_message_id]
    chunks = sources_for_target(all_sources, config.sources,
                                config.sources_per_target) \
        if config.sources_per_target else None
    plans: list[TargetPlan] = []
    for index, target in enumerate(config.targets):
        sources = all_sources
        if config.pairing != "mixed":
            sources = sources_for_pairing(config, index, all_sources,
                                          len(config.targets))
        if chunks:
            allowed = set(chunks[index % len(chunks)])
            sources = [message for message in all_sources
                       if message.peer in allowed]
        keywords = rule_for(config.groups, index)
        collected = Collected(scanned=len(sources))
        for message in sources:
            reason = select(message, keywords, config.senders, config.ignores)
            if reason:
                collected.drop(reason)
                continue
            rules = (rule_for(config.replacement_groups, index)
                     or config.replacements)
            if (rules and message.text and not config.lightweight_mode):
                # Replace on a copy: the same capture feeds every target, and
                # a second pass would apply the rules twice.
                message = replace(
                    message, text=replace_keywords(message.text, rules))
            collected.kept.append(message)
        if index < len(config.account_groups) and config.account_groups[index]:
            accounts = config.account_groups[index]
        else:
            accounts = labels
        plans.append(TargetPlan(target=target, keywords=keywords,
                                collected=collected, accounts=accounts))
    return plans


def effective_limit(config: CopyConfig, is_text: bool) -> int:
    """The cap that applies to this message: ``daily_limit`` or the tighter one.

    The page has two daily numbers, and they are not the same knob:
    ``dailyLimit`` caps everything an account sends in a day, while
    ``dailyTextLimitPerAccount`` ("a per-account cap under the task cap") only
    counts text. A text message therefore obeys whichever is smaller, and media
    obeys the task cap alone.
    """
    limits = [value for value in (
        config.daily_limit,
        config.daily_text_limit_per_account if is_text else 0,
    ) if value > 0]
    return min(limits) if limits else 0


def pick(accounts: list[str], budget: dict[str, int], limit: int,
         offset: int) -> str | None:
    """The next account of this target that still has budget today."""
    if not accounts:
        return None
    start = offset % len(accounts)
    for step in range(len(accounts)):
        candidate = accounts[(start + step) % len(accounts)]
        if not limit or budget.get(candidate, 0) < limit:
            return candidate
    return None


def poll_count(minutes: int, interval: int) -> int:
    """How many collector polls a capture window gets.

    ``collectionIntervalMinutes`` is "how often the collector polls", so a window
    of N minutes with an interval of M polls ``ceil(N/M)`` times; no interval
    means one poll that takes the whole window.
    """
    if interval <= 0 or minutes <= 0:
        return 1
    return max(1, -(-minutes // interval))


def capture(sender: object, account: str, sources: list[str], minutes: int,
            limit: int = CAPTURE_LIMIT, interval: int = 0,
            sleep: object = time.sleep) -> tuple[list[SourceMessage],
                                                 list[tuple[str, str]]]:
    """Read the source chats live; ``(messages, failures)``.

    The capture window is the page's ``copy back for N minutes``; the limit is
    how many messages one source gives up so a busy chat cannot stall the run.
    A source that cannot be read is returned as a failure with its code, not
    dropped: an empty capture and an empty chat must not look the same.

    With ``collectionIntervalMinutes`` set the window is polled instead of read
    once: each poll asks for the last ``interval`` minutes and repeats are
    dropped by message id, so a slow chat is still collected as it arrives.
    """
    media_dir = str(Path("data") / "logs" / "copied")
    failures: list[tuple[str, str]] = []
    seen: set[tuple[str, int]] = set()
    messages: list[SourceMessage] = []
    polls = poll_count(minutes, interval)
    window = interval if interval > 0 else minutes
    for index in range(polls):
        if index:
            print(f"collector: sleeping {interval} min before poll "
                  f"{index + 1}/{polls}")
            sleep(interval * 60)  # type: ignore[operator]
        entries: list[str] = []
        for source in sources:
            target = parse_many([source], "group")[0]
            result = sender.history(account, target,  # type: ignore[attr-defined]
                                    minutes=window, limit=limit,
                                    media_dir=media_dir)
            if not result.ok:
                if (source, result.code) not in failures:
                    failures.append((source, result.code))
                print(f"source={source} capture failed: {result.code}")
                continue
            entries.extend(result.items)
            print(f"source={source} captured {len(result.items)}/{result.count} "
                  f"minutes={window} limit={limit}"
                  + (f" poll={index + 1}/{polls}" if polls > 1 else ""))
        fresh = 0
        for message in parse_source_messages(entries):
            key = (message.peer, message.message_id)
            if key in seen:
                continue
            seen.add(key)
            messages.append(message)
            fresh += 1
        if polls > 1:
            print(f"collector poll {index + 1}/{polls}: +{fresh} new message(s)")
    return messages, failures


def run(config: CopyConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts] or ["-"]
    sender = ctx.sender()
    failures: list[tuple[str, str]] = []
    if ctx.live and not config.messages and config.sources and accounts:
        messages, failures = capture(sender, labels[0], config.sources,
                                     config.capture_minutes,
                                     interval=config.collection_interval_minutes)
        config = replace(config, messages=messages)
        if not messages and failures:
            print("ERROR: no source history was captured: "
                  + ", ".join(f"{source}={code}" for source, code in failures))
            return 1
    plans = plan_targets(config, labels)
    total = sum(len(plan.collected.kept) for plan in plans) * config.multiplier

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        targets=len(config.targets),
        sources=f"{len(config.sources)}/{SOURCE_LIMIT}",
        captured=len(config.messages),
        copy_mode=config.copy_mode,
        pairing=config.pairing,
        capture_minutes=config.capture_minutes,
        multiplier=config.multiplier,
        polish=config.polish,
        interval=f"{config.interval_min:.0f}-{config.interval_max:.0f}s",
        groups=len(config.groups),
        queue=total,
        sources_per_target=config.sources_per_target or "-",
        daily_text_limit=config.daily_text_limit_per_account or "-",
        collection_interval=(f"{config.collection_interval_minutes} min"
                             if config.collection_interval_minutes else "-"),
        crossover=(config.crossover_message_id or "-"),
    )
    if config.pairing in GATED_MODES:
        # The reference refuses these two modes without a permission. This
        # rebuild has no licence layer at all, so the gate is reported instead
        # of enforced - and the mode still behaves as the page describes.
        print(f"permission: {GATED_MODES[config.pairing]} "
              f"(reference gate, not enforced here)")
    on = [name for name, value in config.ignores.items() if value]
    print("忽略开关：" + ("  ".join(on) if on else "全部关闭"))
    level = polish_level(config)
    if level:
        mode = "applied when live" if not ctx.live else "applying now"
        print(f"ai polish level: {level} ({mode})")
    if config.message_speed:
        print(f"message speed: {config.message_speed}/min")
    if config.lightweight_mode:
        print("lightweight mode: no rewrite, no replacements")

    task = open_task(ctx, config.task_name, FEATURE,
                     {"targets": config.targets,
                      "sources": config.sources}, total)
    if ctx.live and level:
        polished = polish_plan(plans, AIClient(ctx.settings.ai,
                                              ctx.settings.ai.timeout),
                               level, config.ai_polish_batch_size)
        print(f"polished: {polished} message(s) at level {level}")
    rng = random.Random()
    ok = failed = skipped = 0
    waited = 0.0
    index = 0
    budget: dict[str, int] = {}

    for plan in plans:
        print(f"target={plan.target} keywords={plan.keywords or '-'} "
              f"{plan.collected.summary()}")
        target: Target = parse_many([plan.target], "custom")[0]
        for message in plan.collected.kept:
            for _ in range(config.multiplier):
                limit = effective_limit(config, bool(message.text))
                account = pick(plan.accounts, budget, limit, index)
                if account is None:
                    skipped += 1
                    index += 1
                    print(f"skipped: no account under the daily limit "
                          f"({limit}) for {target.display()}")
                    continue
                index += 1
                delay = rng.uniform(config.interval_min, config.interval_max)
                if config.message_speed > 0:
                    # The page's messageSpeed is a ceiling, so it can only make
                    # the wait longer: turning it up never floods an account.
                    delay = max(delay, 60.0 / config.message_speed)
                waited += delay
                body = Content(text=message.text, media=message.media)
                pause(ctx, delay)
                outcome = sender.send(account, target, body)
                if outcome.ok:
                    ok += 1
                    budget[account] = budget.get(account, 0) + 1
                    record(ctx, task, account, target.display(), True,
                           detail=message.sender)
                    state = "ok"
                else:
                    failed += 1
                    record(ctx, task, account, target.display(), False,
                           outcome.code, detail=message.sender)
                    state = outcome.code
                print(f"[{index:03d}/{total:03d}] target={target.display()} "
                      f"account={account} source={message.sender} "
                      f"delay={delay:.0f}s text={message.text[:40]} -> {state}")

    print(f"Processed: {ok + failed + skipped} / {total}")
    print(f"ok: {ok}")
    print(f"failed: {failed}")
    print(f"skipped: {skipped}")
    print(f"waited: {format_duration(waited)}")
    print(f"PLAN: {total} messages, {len(labels)} accounts, "
          f"ETA {format_duration(waited)}")
    close_task(ctx, task, ok=ok, failed=failed)
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(CopyConfig.from_dict(raw, ctx.settings), ctx)


__all__ = [
    "CAPTURE_LIMIT",
    "CopyConfig",
    "SOURCE_LIMIT",
    "TITLE",
    "account_groups",
    "capture",
    "keyword_groups",
    "main",
    "pick",
    "plan_targets",
    "rule_for",
    "run",
    "select",
]

