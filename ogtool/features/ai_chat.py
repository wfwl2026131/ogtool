"""#2 AI原创炒群 — generated chatter, with a news channel for seasoning.

The page carries three numbers (speaking interval range and the chance a
message pulls in something from an external news channel) plus a topic box
where blank lines separate one topic from the next.  The wording itself comes
from ``prompts/ai_chat.txt``; without a key the template fallback keeps the
plan printable, and a failed call never stops the task.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..config import ConfigError, Settings
from ..flags import Flag
from ..limiter import format_duration
from ..prompts import compose
from ..runtime import RunContext
from ..senders import Content
from ..sources import from_config
from ..targets import Target, parse_many
from . import register
from ._common import (
    accounts_of,
    ai_client,
    blocks,
    close_task,
    header,
    name_of,
    num,
    open_task,
    record,
    record_ai,
    section,
    targets_of,
    text,
)

FEATURE = "ai_chat"
SECTION = "ai_chat"
TITLE = "#2 AI原创炒群"

FLAGS = (
    Flag("--targets", "targets", str, "targets, comma separated"),
    Flag("--targets-file", "files", str, "file with one target per line",
         action="append"),
    Flag("--interval-min", "interval_min", float, "minimum speaking interval"),
    Flag("--interval-max", "interval_max", float, "maximum speaking interval"),
    Flag("--news-channel", "news_channel", str, "external news channel"),
    Flag("--insert-probability", "insert_probability", int,
         "percent of messages that pull in a news item"),
    Flag("--topics", "topics", str, "topic prompts, blank line separated"),
    Flag("--count", "count", int, "messages per target"),
)

DEFAULT_TOPIC = "日常闲聊"


@dataclass(slots=True)
class AiChatConfig:
    account_labels: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    interval_min: float = 20.0
    interval_max: float = 60.0
    news_channel: str = ""
    insert_probability: int = 0
    topics: list[str] = field(default_factory=list)
    news: list[str] = field(default_factory=list)
    count: int = 3
    task_name: str = "ai-chat"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> AiChatConfig:
        del settings
        data = section(raw, SECTION)
        targets = [target.ref for target in targets_of(data)]
        if not targets:
            raise ConfigError(f"{SECTION}: no target given (--targets)")
        probability = num(data, "insert_probability", 0, kind=int,
                          minimum=0)
        return cls(
            account_labels=accounts_of(raw, data),
            targets=targets,
            interval_min=num(data, "interval_min", 20.0),
            interval_max=num(data, "interval_max", 60.0),
            news_channel=text(data, "news_channel"),
            insert_probability=min(100, probability),
            topics=blocks(data, "topics"),
            news=[item.text for item in from_config(data, "news")],
            count=num(data, "count", 3, kind=int, minimum=1),
            task_name=name_of(raw, data, "ai-chat"),
        )


@dataclass(slots=True)
class Line:
    """One planned message: who says it, where, about what."""

    account: str
    target: str
    topic: str
    news: str = ""
    delay: float = 0.0


def plan(config: AiChatConfig, labels: list[str],
         rng: random.Random | None = None) -> list[Line]:
    """Every message the run would send, in order, with its news decision."""
    generator = rng or random.Random()
    lines: list[Line] = []
    for target in config.targets:
        for _ in range(config.count):
            index = len(lines)
            topic = (config.topics[index % len(config.topics)]
                     if config.topics else DEFAULT_TOPIC)
            news = ""
            if (config.news_channel and config.news
                    and generator.random() * 100 < config.insert_probability):
                news = config.news[index % len(config.news)]
            account = labels[len(lines) % len(labels)] if labels else "-"
            delay = generator.uniform(config.interval_min, config.interval_max)
            lines.append(Line(account=account, target=target, topic=topic,
                              news=news, delay=delay))
    return lines


def _prompt(ctx: RunContext, line: Line) -> str:
    news = f"可以顺带提到这条新闻：{line.news}" if line.news else ""
    return compose("ai_chat", settings=ctx.settings, topic=line.topic,
                   news=news)


def run(config: AiChatConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts]
    lines = plan(config, labels)

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        targets=len(config.targets),
        interval=f"{config.interval_min:.0f}-{config.interval_max:.0f}s",
        news_channel=config.news_channel or "-",
        insert_probability=f"{config.insert_probability}%",
        topics=len(config.topics),
        queue=len(lines),
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"targets": config.targets}, len(lines))
    sender = ctx.sender()
    ai = ai_client(ctx)
    ok = failed = 0
    waited = 0.0

    for index, line in enumerate(lines, start=1):
        target: Target = parse_many([line.target], "custom")[0]
        generated = ai.generate(_prompt(ctx, line))
        record_ai(ctx, task, line.account, "chat", generated)
        body = Content(text=generated.response)
        outcome = sender.send(line.account, target, body)
        waited += line.delay
        if outcome.ok:
            ok += 1
            record(ctx, task, line.account, target.display(), True)
            state = "ok"
        else:
            failed += 1
            record(ctx, task, line.account, target.display(), False,
                   outcome.code)
            state = outcome.code
        print(f"[{index:03d}/{len(lines):03d}] account={line.account} "
              f"target={target.display()} topic={line.topic} "
              f"delay={line.delay:.0f}s ai={generated.label()} "
              f"text={generated.response[:40]} -> {state}")

    print(f"Processed: {ok + failed} / {len(lines)}")
    print(f"ok: {ok}")
    print(f"failed: {failed}")
    fallback = sum(1 for call in ai.calls if not call.ok)
    print(f"ai calls: {len(ai.calls)} (fallback {fallback})")
    print(f"waited: {format_duration(waited)}")
    print(f"PLAN: {len(lines)} messages, {max(1, len(labels))} accounts, "
          f"ETA {format_duration(waited)}")
    close_task(ctx, task, ok=ok, failed=failed)
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(AiChatConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["AiChatConfig", "DEFAULT_TOPIC", "Line", "TITLE", "main", "plan",
           "run"]
