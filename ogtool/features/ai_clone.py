"""#4 AIclone channel - take one channel's posts to another, rewritten or as is.

The chain is capture -> filter -> rewrite -> schedule, with one extra rule the
page makes visible: a source may map to a specific target (``source/source to target mapping``), and
anything unmapped falls back to the target list in order.  ``max`` caps how many
posts one run carries over, so a dry run stays short even on a busy channel.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace

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
from ..sources import parse_many as parse_source_messages
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
    pause,
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
    Flag("--sources-file", "source_files", str,
         "file with one source per line", action="append"),
    Flag("--targets", "targets", str, "target channels, comma separated"),
    Flag("--targets-file", "target_files", str,
         "file with one target per line", action="append"),
    Flag("--mode", "mode", str, "copy, rewrite or ai"),
    Flag("--mapping", "mapping", str, "source=target lines"),
    Flag("--max-items", "max_items", int, "how many posts one run carries"),
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
    post_interval_min: float = 10.0
    post_interval_max: float = 30.0
    author: str = ""
    style: str = ""
    keywords: list[str] = field(default_factory=list)
    replacements: list[tuple[str, str]] = field(default_factory=list)
    messages: list[SourceMessage] = field(default_factory=list)
    #: ``cloneMode``: ``pairwise`` (one target each), ``one_to_one`` (strict,
    #: one target per source) or ``multi_to_one`` (every source into the first
    #: target).
    clone_mode: str = ""
    #: ``passive``: follow the source instead of scanning it once.
    passive: bool = False
    #: ``lastSourceMessageId``: continue after this message.
    last_source_message_id: int = 0
    #: ``totalCloned``: the counter the page shows; carried for the export.
    total_cloned: int = 0
    task_name: str = "ai-clone"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> CloneConfig:
        del settings
        data = section(raw, SECTION)
        targets = [target.ref for target in
                   targets_of({"targets": data.get("targets"),
                               "files": data.get("target_files") or []})]
        if not targets:
            raise ConfigError(f"{SECTION}: no target given (--targets)")
        sources = [target.ref for target in
                   targets_of({"targets": data.get("sources"),
                               "files": data.get("source_files") or []},
                              kind="channel")]
        return cls(
            account_labels=accounts_of(raw, data),
            sources=sources,
            targets=targets,
            mode=text(data, "mode", "copy"),
            mapping=mapping_of(data),
            max_items=num(data, "max_items", 10, kind=int, minimum=1),
            post_interval_min=num(data, "post_interval_min", 10.0),
            post_interval_max=num(data, "post_interval_max", 30.0),
            author=text(data, "author"),
            style=text(data, "style"),
            keywords=_split(data.get("filter_keywords")),
            replacements=parse_replacements(_lines(data.get("replacements"))),
            messages=from_config(data),
            clone_mode=clone_mode_of(data),
            passive=bool(data.get("passive", False)),
            last_source_message_id=num(data, "last_source_message_id", 0,
                                       kind=int, minimum=0),
            total_cloned=num(data, "total_cloned", 0, kind=int, minimum=0),
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


def mapping_of(data: dict) -> dict[str, str]:
    """The source->target map, from either ``mapping`` or the page's ``pairs``.

    ``pairs`` is a list of objects (``sourceRaw`` / ``targetRaw`` / ``pairIndex``)
    in the stored task; a run may also hand over the same list of
    ``source=target`` strings, which is what the field holds in practice.
    """
    mapping = parse_mapping(data.get("mapping"))
    pairs = data.get("pairs")
    if not pairs:
        return mapping
    for pair in pairs if isinstance(pairs, (list, tuple)) else [pairs]:
        if isinstance(pair, dict):
            source = str(pair.get("sourceRaw") or pair.get("source") or "")
            target = str(pair.get("targetRaw") or pair.get("target") or "")
            if source.strip() and target.strip():
                mapping[source.strip()] = target.strip()
            continue
        body = str(pair).strip()
        if "=" in body:
            left, right = body.split("=", 1)
            if left.strip() and right.strip():
                mapping[left.strip()] = right.strip()
    return mapping


def clone_mode_of(data: dict) -> str:
    """``cloneMode``: pairwise, one_to_one or multi_to_one.

    The dump spells the two extremes ``pairwise`` and ``multi-to-one`` and the
    strict middle one ``one_to_one``; a dash and an underscore are the same
    word here.
    """
    raw = str(data.get("clone_mode") or "").strip().lower().replace("-", "_")
    if not raw:
        return ""
    if raw not in ("pairwise", "one_to_one", "multi_to_one", "pair", "one2one"):
        raise ConfigError(f"{SECTION}: clone_mode must be pairwise, one_to_one "
                          f"or multi_to_one, is {raw}")
    return {"pair": "pairwise", "one2one": "one_to_one"}.get(raw, raw)


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
        # ``lastSourceMessageId``: the reference stores the id it reached inside
        # the pair, so the next run starts after it instead of re-copying the
        # same posts. Honoured here the same way: anything at or below the
        # watermark is not a candidate, and the drop is reported rather than
        # silently missing from the count.
        if config.last_source_message_id and \
                message.message_id <= config.last_source_message_id:
            collected.drop("resume")
            continue
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
            target = _fallback_target(config, message.peer, index)
        account = labels[index % len(labels)] if labels else "-"
        delay = generator.uniform(config.post_interval_min,
                                  config.post_interval_max)
        items.append(Item(account=account, source=message, target=target,
                          delay=delay, text=message.text))
    collected.kept = kept
    return items, collected


def _fallback_target(config: CloneConfig, source: str, index: int) -> str:
    """Where an unmapped post goes, per ``cloneMode``."""
    if config.clone_mode == "multi_to_one":
        return config.targets[0]
    if config.clone_mode == "one_to_one":
        position = (config.sources.index(source)
                    if source in config.sources else index)
        return config.targets[position % len(config.targets)]
    return config.targets[index % len(config.targets)]


def _prompt(ctx: RunContext, config: CloneConfig, item: Item) -> str:
    return compose("ai_clone", settings=ctx.settings, text=item.text,
                   style=config.style or "keep the source style",
                   author=config.author or "-")


def capture(sender: object, account: str,
            sources: list[str], limit: int) -> tuple[list[SourceMessage],
                                                     list[tuple[str, str]]]:
    """Read the source channels live: the last ``limit`` posts of each.

    The page has no time window on this one -- ``max`` is how many posts a run
    carries -- so the limit is both the read size and the carry size.  A source
    that cannot be read comes back as a failure with its code.
    """
    entries: list[str] = []
    failures: list[tuple[str, str]] = []
    for source in sources:
        target = parse_many([source], "channel")[0]
        result = sender.history(account, target, minutes=0,  # type: ignore[attr-defined]
                                limit=limit)
        if not result.ok:
            failures.append((source, result.code))
            print(f"source={source} capture failed: {result.code}")
            continue
        entries.extend(result.items)
        print(f"source={source} captured {len(result.items)}/{result.count} "
              f"limit={limit}")
    return parse_source_messages(entries), failures


def run(config: CloneConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts]
    failures: list[tuple[str, str]] = []
    if ctx.live and not config.messages and config.sources and accounts:
        messages, failures = capture(ctx.sender(), labels[0], config.sources,
                                     config.max_items)
        config = replace(config, messages=messages)
        if not messages and failures:
            print("ERROR: no source history was captured: "
                  + ", ".join(f"{source}={code}" for source, code in failures))
            return 1
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
        passive="on" if config.passive else "off",
        resume_after=config.last_source_message_id or "-",
        max_items=config.max_items,
        post_interval=f"{config.post_interval_min:.0f}-"
                      f"{config.post_interval_max:.0f}s",
        style=config.style or "-",
        captured=collected.summary(),
        queue=len(items),
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"sources": config.sources, "targets": config.targets,
                      "resume_after": config.last_source_message_id},
                     len(items))
    sender = ctx.sender()
    ai = ai_client(ctx) if rewriting else None
    ok = failed = 0
    waited = 0.0
    fallback = 0
    landed: list[int] = []

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
        pause(ctx, item.delay)
        outcome = sender.send(item.account, target,
                              Content(text=body_text,
                                      media=item.source.media))
        waited += item.delay
        if outcome.ok:
            ok += 1
            if item.source.message_id:
                landed.append(item.source.message_id)
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
    # ``totalCloned`` / ``lastSourceMessageId``: the counter and the watermark the
    # page keeps in the saved pair. We do not rewrite the task file, so both are
    # printed as the values to store, and the watermark only counts posts that
    # actually landed - advancing it past a failed post would lose that post.
    watermark = max(landed, default=0)
    if config.last_source_message_id or watermark:
        print(f"resume: next run continues after "
              f"{watermark or config.last_source_message_id}")
    total = config.total_cloned + ok
    if config.total_cloned or ok:
        print(f"total cloned: {total} (this run +{ok})")
    print(f"PLAN: {len(items)} posts, {max(1, len(labels))} accounts, "
          f"ETA {format_duration(waited)}")
    close_task(ctx, task, ok=ok, failed=failed)
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(CloneConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["CloneConfig", "Item", "TITLE", "capture", "main", "parse_mapping",
           "plan", "run"]
