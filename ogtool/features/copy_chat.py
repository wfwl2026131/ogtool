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
from dataclasses import dataclass, field, replace
from pathlib import Path

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
    ai_polish_level: int = 0
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
            capture_minutes=num(data, "capture_minutes", 60, kind=int,
                                minimum=0),
            daily_limit=num(data, "daily_limit", 0, kind=int, minimum=0),
            multiplier=num(data, "multiplier", 1, kind=int, minimum=1),
            polish=num(data, "polish", 0, kind=int, minimum=0),
            interval_min=num(data, "interval_min", 5.0),
            interval_max=num(data, "interval_max", 12.0),
            groups=keyword_groups(data.get("filter_keywords")),
            senders=_split(data.get("filter_senders")),
            replacements=parse_replacements(_lines(data.get("replacements"))),
            account_groups=account_groups(data.get("account_groups")),
            messages=from_config(data),
            ignores=read_ignores(data),
            ai_polish_level=num(data, "ai_polish_level", 0, kind=int,
                                minimum=0),
            message_speed=num(data, "message_speed", 0, kind=int, minimum=0),
            lightweight_mode=bool(data.get("lightweight_mode")),
            task_name=name_of(raw, data, "copy-chat"),
        )


def _lines(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return str(value).splitlines()


def read_ignores(data: dict) -> dict[str, bool]:
    """The five switches, as they sit in the page's config."""
    return {name: bool(data.get(name)) for name in IGNORE_SENDERS}


def _split(value: object) -> list[str]:
    return [part.strip() for part in ",".join(_lines(value)).split(",")
            if part.strip()]


@dataclass(slots=True)
class TargetPlan:
    target: str
    keywords: list[str]
    collected: Collected
    accounts: list[str]


def plan_targets(config: CopyConfig, labels: list[str]) -> list[TargetPlan]:
    """One plan per target: its rule group, its survivors, its accounts."""
    sources = by_peer(config.messages, config.sources)
    plans: list[TargetPlan] = []
    for index, target in enumerate(config.targets):
        keywords = rule_for(config.groups, index)
        collected = Collected(scanned=len(sources))
        for message in sources:
            reason = select(message, keywords, config.senders, config.ignores)
            if reason:
                collected.drop(reason)
                continue
            if (config.replacements and message.text
                    and not config.lightweight_mode):
                # Replace on a copy: the same capture feeds every target, and
                # a second pass would apply the rules twice.
                message = replace(
                    message, text=replace_keywords(message.text,
                                                   config.replacements))
            collected.kept.append(message)
        if index < len(config.account_groups) and config.account_groups[index]:
            accounts = config.account_groups[index]
        else:
            accounts = labels
        plans.append(TargetPlan(target=target, keywords=keywords,
                                collected=collected, accounts=accounts))
    return plans


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


def capture(sender: object, account: str, sources: list[str], minutes: int,
            limit: int = CAPTURE_LIMIT) -> tuple[list[SourceMessage],
                                                list[tuple[str, str]]]:
    """Read the source chats live; ``(messages, failures)``.

    The capture window is the page's ``copy back for N minutes``; the limit is
    how many messages one source gives up so a busy chat cannot stall the run.
    A source that cannot be read is returned as a failure with its code, not
    dropped: an empty capture and an empty chat must not look the same.
    """
    entries: list[str] = []
    media_dir = str(Path("data") / "logs" / "copied")
    failures: list[tuple[str, str]] = []
    for source in sources:
        target = parse_many([source], "group")[0]
        result = sender.history(account, target,  # type: ignore[attr-defined]
                                minutes=minutes, limit=limit,
                                media_dir=media_dir)
        if not result.ok:
            failures.append((source, result.code))
            print(f"source={source} capture failed: {result.code}")
            continue
        entries.extend(result.items)
        print(f"source={source} captured {len(result.items)}/{result.count} "
              f"minutes={minutes} limit={limit}")
    return parse_source_messages(entries), failures


def run(config: CopyConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts] or ["-"]
    sender = ctx.sender()
    failures: list[tuple[str, str]] = []
    if ctx.live and not config.messages and config.sources and accounts:
        messages, failures = capture(sender, labels[0], config.sources,
                                     config.capture_minutes)
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
        capture_minutes=config.capture_minutes,
        multiplier=config.multiplier,
        polish=config.polish,
        interval=f"{config.interval_min:.0f}-{config.interval_max:.0f}s",
        groups=len(config.groups),
        queue=total,
    )
    on = [name for name, value in config.ignores.items() if value]
    print("忽略开关：" + ("  ".join(on) if on else "全部关闭"))
    if config.ai_polish_level:
        print(f"ai polish level: {config.ai_polish_level}")
    if config.message_speed:
        print(f"message speed: {config.message_speed}/min")
    if config.lightweight_mode:
        print("lightweight mode: no rewrite, no replacements")

    task = open_task(ctx, config.task_name, FEATURE,
                     {"targets": config.targets,
                      "sources": config.sources}, total)
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
                account = pick(plan.accounts, budget, config.daily_limit,
                               index)
                if account is None:
                    skipped += 1
                    index += 1
                    print(f"skipped: no account under the daily limit "
                          f"({config.daily_limit}) for {target.display()}")
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

