"""#4 AI搬运频道 — take one channel's posts to another, rewritten or as is.

The chain is capture -> filter -> rewrite -> schedule, with one extra rule the
page makes visible: a source may map to a specific target (``源/目标映射``), and
anything unmapped falls back to the target list in order.  ``最多`` caps how many
posts one run carries over, so a dry run stays short even on a busy channel.
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
from ..sources import (
    Collected,
    SourceMessage,
    by_peer,
    from_config,
    keep,
    parse_replacements,
    replace_keywords,
)
from ..targets import Target, parse_many
from . import register
from ._common import (
    accounts_of,
    ai_client,
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

FEATURE = "ai_clone"
SECTION = "ai_clone"
TITLE = "#4 AI搬运频道"

FLAGS = (
    Flag("--sources", "sources", str, "source channels, comma separated"),
    Flag("--sources-file", "files", str, "file with one source per line",
         action="append"),
    Flag("--targets", "targets", str, "target channels, comma separated"),
    Flag("--targets-file", "targets_files", str,
         "file with one target per line", action="append"),
    Flag("--mode", "mode", str, "copy, rewrite or ai"),
    Flag("--mapping", "mapping", str, "source=target lines"),
    Flag("--max-items", "max_items", int, "how many posts one run carries"),
    Flag("--poll-interval", "poll_interval", float, "seconds between polls"),
    Flag("--post-interval-min", "post_interval_min", float,
         "minimum seconds between two posts"),
    Flag("--post-interval-max", "post_interval_max", float,
         "maximum seconds between two posts"),
    Flag("--author", "author", str, "author line kept with the post"),
    Flag("--style", "style", str, "the style the rewrite should follow"),
    Flag("--filter-keywords", "filter_keywords", str,
         "drop a post when it contains one of these, one per line"),
    Flag("--replacements", "replacements", str, "old=new rewrites"),
    Flag("--message", "messages", str,
         "captured post, peer|sender|text (repeatable)", action="append"),
)


@dataclass(slots=True)
class CloneConfig:
    account_labels: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    mode: str = "copy"
    mapping: dict[str, str] = field(default_factory=dict)
    max_items: int = 10
    poll_interval: float = 60.0
    post_interval_min: float = 10.0
    post_interval_max: float = 30.0
    author: str = ""
    style: str = ""
    keywords: list[str] = field(default_factory=list)
    replacements: list[tuple[str, str]] = field(default_factory=list)
    messages: list[SourceMessage] = field(default_factory=list)
    task_name: str = "ai-clone"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> CloneConfig:
        del settings
        data = section(raw, SECTION)
        targets = [target.ref for target in targets_of(data)]
        if not targets:
            raise ConfigError(f"{SECTION}: no target given (--targets)")
        sources = [target.ref for target in targets_of(data, "sources",
                                                       kind="channel")]
        return cls(
            account_labels=accounts_of(raw, data),
            sources=sources,
            targets=targets,
            mode=text(data, "mode", "copy"),
            mapping=parse_mapping(data.get("mapping")),
            max_items=num(data, "max_items", 10, kind=int, minimum=1),
            poll_interval=num(data, "poll_interval", 60.0),
            post_interval_min=num(data, "post_interval_min", 10.0),
            post_interval_max=num(data, "post_interval_max", 30.0),
            author=text(data, "author"),
            style=text(data, "style"),
            keywords=_split(data.get("filter_keywords")),
            replacements=parse_replacements(_lines(data.get("replacements"))),
            messages=from_config(data),
            task_name=name_of(raw, data, "ai-clone"),
        )


def _lines(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return str(value).splitlines()


def _split(value: object) -> list[str]:
    return [part.strip() for part in ",".join(_lines(value)).split(",")
            if part.strip()]


def parse_mapping(value: object) -> dict[str, str]:
    """``source=target`` lines -> the per source override of the target list."""
    pairs: dict[str, str] = {}
    for line in _lines(value):
        body = line.strip()
        if not body:
            continue
        if "=" not in body:
            raise ConfigError(f"mapping needs source=target, got {body!r}")
        left, right = body.split("=", 1)
        if not left.strip() or not right.strip():
            raise ConfigError(f"mapping needs source=target, got {body!r}")
        pairs[left.strip()] = right.strip()
    return pairs


@dataclass(slots=True)
class Item:
    """One post to carry over: where it came from, where it goes, by whom."""

    account: str
    source: SourceMessage
    target: str
    delay: float
    text: str = ""


def plan(config: CloneConfig, labels: list[str],
         rng: random.Random | None = None) -> tuple[list[Item], Collected]:
    """Capture, filter and schedule; returns the items and what was dropped."""
    generator = rng or random.Random()
    messages = by_peer(config.messages, config.sources)
    collected = Collected(scanned=len(messages))
    kept: list[SourceMessage] = []
    for message in messages:
        if not keep(message, config.keywords):
            collected.drop("keyword")
            continue
        if config.replacements and message.text:
            message.text = replace_keywords(message.text, config.replacements)
        kept.append(message)

    items: list[Item] = []
    for index, message in enumerate(kept[: config.max_items]):
        target = config.mapping.get(message.peer)
        if not target:
            target = config.targets[index % len(config.targets)]
        account = labels[index % len(labels)] if labels else "-"
        delay = generator.uniform(config.post_interval_min,
                                  config.post_interval_max)
        items.append(Item(account=account, source=message, target=target,
                          delay=delay, text=message.text))
    collected.kept = kept
    return items, collected


def _prompt(ctx: RunContext, config: CloneConfig, item: Item) -> str:
    return compose("ai_clone", settings=ctx.settings, text=item.text,
                   style=config.style or "保持原文风格",
                   author=config.author or "无")


def run(config: CloneConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts]
    items, collected = plan(config, labels)
    rewriting = config.mode in ("ai", "rewrite")

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        sources=len(config.sources),
        targets=len(config.targets),
        mode=config.mode,
        mapping=len(config.mapping),
        max_items=config.max_items,
        post_interval=f"{config.post_interval_min:.0f}-"
                      f"{config.post_interval_max:.0f}s",
        style=config.style or "-",
        captured=collected.summary(),
        queue=len(items),
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"sources": config.sources, "targets": config.targets},
                     len(items))
    sender = ctx.sender()
    ai = ai_client(ctx) if rewriting else None
    ok = failed = 0
    waited = 0.0
    fallback = 0

    for index, item in enumerate(items, start=1):
        target: Target = parse_many([item.target], "custom")[0]
        body_text = item.text
        label = "copy"
        if rewriting and ai is not None:
            generated = ai.generate(_prompt(ctx, config, item),
                                    fallback=item.text)
            record_ai(ctx, task, item.account, "clone", generated)
            if not generated.ok:
                fallback += 1
            body_text = generated.response
            label = generated.label()
        outcome = sender.send(item.account, target,
                              Content(text=body_text,
                                      media=item.source.media))
        waited += item.delay
        if outcome.ok:
            ok += 1
            record(ctx, task, item.account, target.display(), True,
                   detail=item.source.sender)
            state = "ok"
        else:
            failed += 1
            record(ctx, task, item.account, target.display(), False,
                   outcome.code, detail=item.source.sender)
            state = outcome.code
        print(f"[{index:03d}/{len(items):03d}] account={item.account} "
              f"source={item.source.peer} target={target.display()} "
              f"delay={item.delay:.0f}s {label} text={body_text[:40]} -> {state}")

    print(f"Processed: {ok + failed} / {len(items)}")
    print(f"ok: {ok}")
    print(f"failed: {failed}")
    if rewriting:
        print(f"ai calls: {len(ai.calls) if ai else 0} (fallback {fallback})")
    print(f"waited: {format_duration(waited)}")
    print(f"PLAN: {len(items)} posts, {max(1, len(labels))} accounts, "
          f"ETA {format_duration(waited)}")
    close_task(ctx, task, ok=ok, failed=failed)
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(CloneConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["CloneConfig", "Item", "TITLE", "main", "parse_mapping", "plan",
           "run"]
