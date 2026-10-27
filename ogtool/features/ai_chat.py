"""#2 AIgenerated chatter - generated chatter, with a news channel for seasoning.

The page carries three numbers (speaking interval range and the chance a
message pulls in something from an external news channel) plus a topic box
where blank lines separate one topic from the next.  The wording itself comes
from ``prompts/ai_chat.txt``; without a key the template fallback keeps the
plan printable, and a failed call never stops the task.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace

from ..config import ConfigError, Settings
from ..flags import Flag
from ..limiter import format_duration
from ..runtime import RunContext
from ..senders import Content
from ..sources import SourceMessage
from ..sources import parse_many as parse_sources
from ..targets import Target, parse_many
from ..templates import compose
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
    pause,
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
    Flag("--news", "news", str, "a news item to pull in (repeatable)",
         action="append"),
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
    #: ``topicMinCount``: at least this many topics in one batch.
    topic_min_count: int = 0
    #: ``topicStickiness``: how long one topic stays the subject, in percent.
    topic_stickiness: int = 0
    #: ``chatTextMaxCharsLevels``: sentence length ladder.
    text_max_chars_levels: list[int] = field(default_factory=list)
    #: ``stickerSendPerMille`` / ``emojiSendPerMille``: per-thousand shares.
    sticker_send_per_mille: int = 0
    #: ``stickerShortName`` / ``stickerEmoji``: the pool a sticker comes from and
    #: the emoji that picks one out of it (the page logs "sending sticker by
    #: shortName: %3, emoji: %4").
    sticker_sets: list[str] = field(default_factory=list)
    sticker_emoji: str = ""
    emoji_send_per_mille: int = 0
    #: ``replyEnabled`` / ``allowFollowUp``: answer what is said to us.
    reply_enabled: bool = False
    allow_follow_up: bool = False
    #: ``patternRefreshSeconds``: how often the wording is refreshed.
    pattern_refresh_seconds: int = 0
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
            news=_news(data.get("news")),
            count=num(data, "count", 3, kind=int, minimum=1),
            topic_min_count=num(data, "topic_min_count", 0, kind=int,
                                minimum=0),
            topic_stickiness=min(100, num(data, "topic_stickiness", 0,
                                          kind=int, minimum=0)),
            text_max_chars_levels=[int(item) for item in
                                   data.get("chat_text_max_chars_levels") or []
                                   if str(item).strip().isdigit()],
            sticker_send_per_mille=num(data, "sticker_send_per_mille", 0,
                                       kind=int, minimum=0),
            sticker_sets=[item for item in
                          data.get("sticker_short_names") or []
                          if str(item).strip()]
                         or ([text(data, "sticker_short_name")]
                             if text(data, "sticker_short_name") else []),
            sticker_emoji=text(data, "sticker_emoji"),
            emoji_send_per_mille=num(data, "emoji_send_per_mille", 0,
                                     kind=int, minimum=0),
            reply_enabled=bool(data.get("reply_enabled", False)),
            allow_follow_up=bool(data.get("allow_follow_up", False)),
            pattern_refresh_seconds=num(data, "pattern_refresh_seconds", 0,
                                        kind=int, minimum=0),
            task_name=name_of(raw, data, "ai-chat"),
        )


def _news(value: object) -> list[str]:
    """News items: a YAML list, or one per line."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [line.strip() for line in str(value).splitlines() if line.strip()]


@dataclass(slots=True)
class Line:
    """One planned message: who says it, where, about what."""

    account: str
    target: str
    topic: str
    news: str = ""
    delay: float = 0.0
    #: ``stickerSendPerMille`` drew this line: it is a sticker, not a sentence.
    sticker: bool = False


def plan(config: AiChatConfig, labels: list[str],
         rng: random.Random | None = None) -> list[Line]:
    """Every message the run would send, in order, with its news decision."""
    generator = rng or random.Random()
    lines: list[Line] = []
    topics = config.topics or [DEFAULT_TOPIC]
    rotation = 0
    for target in config.targets:
        topic = topics[0]
        used: set[str] = set()
        for _ in range(config.count):
            index = len(lines)
            # ``topicStickiness``: "how long one topic stays the subject, in
            # percent". Sticking repeats the previous topic instead of advancing
            # the rotation; ``topicMinCount`` keeps a batch from sticking before
            # it has covered that many different topics at all.
            sticky = (index and len(used) >= config.topic_min_count
                      and generator.random() * 100 < config.topic_stickiness)
            if not sticky:
                topic = topics[rotation % len(topics)]
                rotation += 1
            used.add(topic)
            news = ""
            # The channel is where live runs read news from; the items are the
            # pool, so a dry-run with --news alone can still show insertion.
            if (config.news
                    and generator.random() * 100 < config.insert_probability):
                news = config.news[index % len(config.news)]
            account = labels[len(lines) % len(labels)] if labels else "-"
            delay = generator.uniform(config.interval_min, config.interval_max)
            lines.append(Line(account=account, target=target, topic=topic,
                              news=news, delay=delay,
                              sticker=(generator.randrange(1000)
                                       < config.sticker_send_per_mille)))
    return lines


def _prompt(ctx: RunContext, line: Line) -> str:
    news = (compose("ai_chat_news", settings=ctx.settings, news=line.news)
            if line.news else "")
    return compose("ai_chat", settings=ctx.settings, topic=line.topic,
                   news=news)


def _fallback(line: Line) -> str:
    """The line a run sends when no model answered: the topic itself."""
    return " ".join(part for part in (line.topic, line.news) if part)


def refreshed(config: AiChatConfig, labels: list[str],
              count: int) -> list[Line]:
    """Exactly ``count`` fresh lines, for the pattern-refresh swap.

    ``plan`` returns one batch (every target, ``count`` messages each); the swap
    needs the same number of lines as the tail it replaces, so a short batch is
    padded from itself rather than leaving holes in the schedule.
    """
    batch = plan(config, labels)
    if not batch or count <= 0:
        return []
    return [batch[index % len(batch)] for index in range(count)]


def clip(text: str, levels: list[int],
         rng: random.Random | None = None) -> str:
    """Cut a generated line to one of the page's length levels.

    ``textMaxCharsLevels`` is a list, not a single number: each message draws a
    level and is cut to it, at a word boundary so a sentence never ends
    mid-token. No levels means no cap at all.
    """
    flat = " ".join(text.split())
    if not levels:
        return flat
    cap = (rng or random).choice(levels)
    if cap <= 0 or len(flat) <= cap:
        return flat
    cut = flat[:cap]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,.;:!?-—") or flat[:cap]


#: The page's two per-mille switches share one small pool of face emoji: the
#: message keeps its text and gains a trailing face, which is what makes a
#: generated line look typed rather than printed.
BODY_EMOJI = ("\U0001F642", "\U0001F44D", "\U0001F525", "\U0001F4AF")


def with_emoji(text: str, per_mille: int,
               rng: random.Random | None = None) -> str:
    """``emojiSendPerMille``: append one face, ``per_mille`` of a thousand."""
    if per_mille <= 0 or not text:
        return text
    if (rng or random).randrange(1000) >= per_mille:
        return text
    return f"{text} {(rng or random).choice(BODY_EMOJI)}".strip()


NEWS_WINDOW_MINUTES = 360
NEWS_LIMIT = 30

REPLY_WINDOW_MINUTES = 120
REPLY_LIMIT = 20


def incoming(sender: object, account: str, target: Target, labels: list[str],
             config: AiChatConfig) -> list[SourceMessage]:
    """Messages worth answering in one chat, oldest first.

    ``replyEnabled`` turns a one-way chatter task into a conversation, so this
    reads the chat's own history through the sender's ``history`` primitive -
    the same one the copy and collect pages use - and drops anything the pool
    itself said (``sender`` carries the author, which is all the reference gives
    the planner to work with).
    """
    result = sender.history(account, target,  # type: ignore[attr-defined]
                            minutes=REPLY_WINDOW_MINUTES, limit=REPLY_LIMIT)
    if not result.ok:
        print(f"reply read failed on {target.display()}: {result.code}")
        return []
    messages = parse_sources(result.items)
    own = set(labels)
    return [message for message in messages
            if message.text.strip() and message.sender not in own]


def reply_round(config: AiChatConfig, ctx: RunContext, sender: object,
                ai: object, task: int, labels: list[str]) -> tuple[int, int]:
    """Answer what the chat said, ``(answered, failed)``.

    ``allowFollowUp`` decides whether a sender who talks twice in one round gets
    two answers (a conversation) or one (a single reaction), which is the whole
    difference between the page's two switches.
    """
    answered = failed = 0
    for index, entry in enumerate(config.targets, start=1):
        target = parse_many([entry], "custom")[0]
        messages = incoming(sender, labels[0], target, labels, config)
        seen: set[str] = set()
        for message in messages:
            if not config.allow_follow_up and message.sender in seen:
                continue
            seen.add(message.sender)
            prompt = compose("ai_chat", settings=ctx.settings,
                             topic=message.text, news="")
            generated = ai.generate(prompt, fallback=message.text)  # type: ignore[attr-defined]
            record_ai(ctx, task, labels[0], "reply", generated)
            body = Content(
                text=with_emoji(clip(generated.response,
                                     config.text_max_chars_levels),
                                config.emoji_send_per_mille),
                reply_to=message.message_id)
            pause(ctx, config.interval_min)
            outcome = sender.send(labels[0], target, body)  # type: ignore[attr-defined]
            if outcome.ok:
                answered += 1
                record(ctx, task, labels[0], f"{target.display()}#"
                       f"{message.message_id}", True)
                state = "ok"
            else:
                failed += 1
                record(ctx, task, labels[0], f"{target.display()}#"
                       f"{message.message_id}", False, outcome.code)
                state = outcome.code
            print(f"[reply {index:02d}] to={message.sender or '-'} "
                  f"chat={target.display()} msg={message.message_id} "
                  f"ai={generated.label()} -> {state}")
    return answered, failed


def read_news(sender: object, account: str, channel: str,
              limit: int = NEWS_LIMIT) -> list[str]:
    """``newsChannel``: the channel the news items are read from, live only."""
    target = parse_many([channel], "channel")[0]
    result = sender.history(account, target,  # type: ignore[attr-defined]
                            minutes=NEWS_WINDOW_MINUTES, limit=limit)
    if not result.ok:
        print(f"news channel {channel} -> {result.code} (no news read)")
        return []
    messages = parse_sources(result.items)
    return [message.text.strip() for message in messages
            if message.text.strip()]


def send_sticker(sender: object, config: AiChatConfig, line: Line,
                 target: Target) -> object | None:
    """``stickerSendPerMille``: the sticker path, or ``None`` to fall back to text.

    The page logs "sticker intent not matched, skip" when the drawn sticker has
    nowhere to come from, and that is what happens here too: no set, or a set
    that cannot be resolved, means the line is spoken instead - with a line of
    output saying so, so a text-only "sticker run" is never silent.
    """
    if not config.sticker_sets:
        print("sticker intent not matched, skip (no sticker set is configured)")
        return None
    outcome = getattr(sender, "send_sticker", None)
    if outcome is None:
        print("sticker intent not matched, skip (this sender has no sticker path)")
        return None
    result = outcome(line.account, target, config.sticker_sets,
                     emoji=config.sticker_emoji)
    if not result.ok:
        print(f"sticker intent not matched, skip ({result.code}: {result.detail})")
        return None
    return result


def run(config: AiChatConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts]
    if ctx.live and config.news_channel and not config.news and labels:
        # The page reads its news live; a dry run keeps to the supplied pool so
        # the plan stays reproducible.
        config = replace(config, news=read_news(ctx.sender(), labels[0],
                                                config.news_channel))
        print(f"news: {len(config.news)} item(s) read from "
              f"{config.news_channel}")
    lines = plan(config, labels)

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        targets=len(config.targets),
        interval=f"{config.interval_min:.0f}-{config.interval_max:.0f}s",
        news_channel=config.news_channel or "-",
        insert_probability=f"{config.insert_probability}%",
        news_items=len(config.news),
        topics=len(config.topics),
        topic_min_count=config.topic_min_count or "-",
        topic_stickiness=f"{config.topic_stickiness}%"
                         if config.topic_stickiness else "-",
        text_levels=(", ".join(str(item) for item in
                               config.text_max_chars_levels) or "-"),
        per_mille=f"sticker {config.sticker_send_per_mille} / "
                  f"emoji {config.emoji_send_per_mille}",
        replies=("on" if config.reply_enabled else "off")
                + (" +follow-up" if config.allow_follow_up else ""),
        refresh=f"{config.pattern_refresh_seconds}s"
                if config.pattern_refresh_seconds else "-",
        queue=len(lines),
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"targets": config.targets}, len(lines))
    sender = ctx.sender()
    ai = ai_client(ctx)
    ok = failed = 0
    stickers = 0
    waited = 0.0
    # ``patternRefreshSeconds``: the reference re-asks for a wording pattern on a
    # timer ("topic prompt changed, refresh pattern"), so a long run does not
    # keep sending the batch that was planned at the start.
    refresh = config.pattern_refresh_seconds
    next_refresh = float(refresh) if refresh else 0.0

    for index, line in enumerate(lines, start=1):
        if refresh and waited >= next_refresh and index < len(lines):
            tail = len(lines) - index + 1
            lines[index - 1:] = refreshed(config, labels, tail)
            next_refresh = waited + refresh
            print(f"refresh: topic prompt changed after {waited:.0f}s, "
                  f"{tail} line(s) replanned")
            line = lines[index - 1]
        target: Target = parse_many([line.target], "custom")[0]
        if line.sticker:
            sticker = send_sticker(sender, config, line, target)
            if sticker is not None:
                paused = line.delay
                pause(ctx, paused)
                waited += paused
                ok += 1
                stickers += 1
                record(ctx, task, line.account, target.display(), True,
                       detail=f"sticker {sticker.detail}")
                print(f"[{index:03d}/{len(lines):03d}] sticker "
                      f"{line.account} -> {target.display()} {sticker.detail}")
                continue
        generated = ai.generate(_prompt(ctx, line),
                                fallback=_fallback(line))
        record_ai(ctx, task, line.account, "chat", generated)
        body = Content(
            text=with_emoji(clip(generated.response,
                                 config.text_max_chars_levels),
                            config.emoji_send_per_mille))
        pause(ctx, line.delay)
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

    answered = reply_failed = 0
    if config.reply_enabled and ctx.live:
        # ``replyEnabled``: after talking into the chat, answer what it said
        # back. A dry run never reads history, so it never claims replies.
        answered, reply_failed = reply_round(config, ctx, sender, ai, task,
                                             labels)
    elif config.reply_enabled:
        print("reply: on (a dry run reads no history, so it answers nothing)")

    print(f"Processed: {ok + failed} / {len(lines)}")
    print(f"ok: {ok}")
    print(f"failed: {failed}")
    if config.sticker_send_per_mille:
        print(f"stickers: {stickers}")
    if config.reply_enabled:
        print(f"replies: {answered} answered, {reply_failed} failed")
    fallback = sum(1 for call in ai.calls if not call.ok)
    print(f"ai calls: {len(ai.calls)} (fallback {fallback})")
    print(f"waited: {format_duration(waited)}")
    print(f"PLAN: {len(lines)} messages, {max(1, len(labels))} accounts, "
          f"ETA {format_duration(waited)}")
    close_task(ctx, task, ok=ok + answered, failed=failed + reply_failed)
    return 0 if not (failed or reply_failed) else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(AiChatConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["AiChatConfig", "DEFAULT_TOPIC", "Line", "TITLE", "main", "plan",
           "run"]
