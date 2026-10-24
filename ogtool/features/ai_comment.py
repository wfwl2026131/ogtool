"""#3 AIsmart comments - comment on a channel's recent posts, with the ratios the page shows.

The page is unusual in that half its numbers are *proportions*: how many
comments are upbeat, how often a comment carries an emoji, and how many likes a
comment gets.  Those are planned before anything is generated, so a dry run can
be checked statistically (100 planned comments must land inside the configured
windows), and a missing key only changes the wording, not the distribution.
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
from ..sources import SourceMessage, from_config, synthetic
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
    pause,
    record,
    record_ai,
    section,
    targets_of,
    text,
)

FEATURE = "ai_comment"
SECTION = "ai_comment"
TITLE = "#3 AI智能评论"

FLAGS = (
    Flag("--targets", "targets", str, "channel or post links, comma separated"),
    Flag("--targets-file", "files", str, "file with one target per line",
         action="append"),
    Flag("--comment-interval-min", "comment_interval_min", float,
         "minimum seconds between two comments"),
    Flag("--comment-interval-max", "comment_interval_max", float,
         "maximum seconds between two comments"),
    Flag("--per-post-min", "per_post_min", int, "comments per post, minimum"),
    Flag("--per-post-max", "per_post_max", int, "comments per post, maximum"),
    Flag("--positive-ratio", "positive_ratio", int,
         "percent of comments that are upbeat"),
    Flag("--likes-min", "likes_min", int, "likes per comment, minimum"),
    Flag("--likes-max", "likes_max", int, "likes per comment, maximum"),
    Flag("--emoji-probability", "emoji_probability", int,
         "percent of comments that carry an emoji"),
    Flag("--like-emoji", "like_emoji", str, "the reaction used for likes"),
    Flag("--max-chars", "max_chars", int, "comment length limit"),
    Flag("--poll-interval", "poll_interval", float, "seconds between polls"),
    Flag("--poll-recent", "poll_recent", int, "how many recent posts to poll"),
    Flag("--extra-prompts", "extra_prompts", str,
         "extra instructions, blank line separated"),
    Flag("--posts", "posts", int, "simulated posts per target"),
    Flag("--seed", "seed", int, "random seed, for a reproducible plan"),
)

POSITIVE = "positive"

#: Emoji a comment may end with, and the reaction an account leaves on the post.
#: These are two different things: the first is part of the text, the second is
#: a reaction the server records separately from it.
BODY_EMOJI = ("\U0001F642", "\U0001F44D", "\U0001F525", "\U0001F4AF")
DEFAULT_REACTION = "\U0001F44D"
NEUTRAL = "neutral"


@dataclass(slots=True)
class CommentConfig:
    account_labels: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    comment_interval_min: float = 15.0
    comment_interval_max: float = 45.0
    per_post_min: int = 1
    per_post_max: int = 2
    positive_ratio: int = 60
    likes_min: int = 0
    likes_max: int = 3
    emoji_probability: int = 30
    like_emoji: str = ""
    max_chars: int = 80
    poll_interval: float = 60.0
    poll_recent: int = 3
    extra_prompts: list[str] = field(default_factory=list)
    posts: int = 3
    messages: list[SourceMessage] = field(default_factory=list)
    seed: int | None = None
    task_name: str = "ai-comment"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> CommentConfig:
        del settings
        data = section(raw, SECTION)
        targets = [target.ref for target in targets_of(data)]
        if not targets:
            raise ConfigError(f"{SECTION}: no target given (--targets)")
        seed = data.get("seed")
        return cls(
            account_labels=accounts_of(raw, data),
            targets=targets,
            comment_interval_min=num(data, "comment_interval_min", 15.0),
            comment_interval_max=num(data, "comment_interval_max", 45.0),
            per_post_min=num(data, "per_post_min", 1, kind=int, minimum=0),
            per_post_max=num(data, "per_post_max", 2, kind=int, minimum=0),
            positive_ratio=min(100, num(data, "positive_ratio", 60, kind=int,
                                        minimum=0)),
            likes_min=num(data, "likes_min", 0, kind=int, minimum=0),
            likes_max=num(data, "likes_max", 3, kind=int, minimum=0),
            emoji_probability=min(100, num(data, "emoji_probability", 30,
                                           kind=int, minimum=0)),
            like_emoji=text(data, "like_emoji"),
            max_chars=num(data, "max_chars", 80, kind=int, minimum=1),
            poll_interval=num(data, "poll_interval", 60.0),
            poll_recent=num(data, "poll_recent", 3, kind=int, minimum=1),
            extra_prompts=blocks(data, "extra_prompts"),
            posts=num(data, "posts", 3, kind=int, minimum=0),
            messages=from_config(data),
            seed=int(seed) if seed is not None and str(seed).strip() else None,
            task_name=name_of(raw, data, "ai-comment"),
        )


@dataclass(slots=True)
class CommentSpec:
    """One planned comment, before any wording exists."""

    account: str
    target: str
    post_id: int
    tone: str
    likes: int
    emoji: bool
    delay: float


@dataclass(slots=True)
class PlanStats:
    specs: list[CommentSpec] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.specs)

    @property
    def positive(self) -> int:
        return sum(1 for spec in self.specs if spec.tone == POSITIVE)

    @property
    def emoji(self) -> int:
        return sum(1 for spec in self.specs if spec.emoji)

    def ratio(self, value: int) -> float:
        return 0.0 if not self.total else value * 100.0 / self.total


def posts_for(config: CommentConfig, target: str) -> list[SourceMessage]:
    """The posts to comment on: the supplied ones, else synthetic stand ins."""
    chosen = [message for message in config.messages
              if message.peer == target or not config.messages]
    if config.messages and not chosen:
        chosen = list(config.messages)
    if not chosen:
        return synthetic(target, config.posts)
    return chosen[: config.poll_recent or len(chosen)]


def plan(config: CommentConfig, labels: list[str],
         rng: random.Random | None = None) -> PlanStats:
    """Every comment the run would write, with its tone, likes and emoji."""
    generator = rng or random.Random(config.seed)
    stats = PlanStats()
    low = min(config.per_post_min, config.per_post_max)
    high = max(config.per_post_min, config.per_post_max)
    likes_low = min(config.likes_min, config.likes_max)
    likes_high = max(config.likes_min, config.likes_max)

    for target in config.targets:
        posts = posts_for(config, target)
        for post in posts:
            count = generator.randint(low, high)
            for _ in range(count):
                tone = (POSITIVE if generator.random() * 100
                        < config.positive_ratio else NEUTRAL)
                emoji = generator.random() * 100 < config.emoji_probability
                likes = generator.randint(likes_low, likes_high)
                account = labels[len(stats.specs) % len(labels)] if labels else "-"
                delay = generator.uniform(config.comment_interval_min,
                                          config.comment_interval_max)
                stats.specs.append(CommentSpec(
                    account=account, target=target,
                    post_id=post.message_id, tone=tone, likes=likes,
                    emoji=emoji, delay=delay,
                ))
    return stats


def _prompt(ctx: RunContext, config: CommentConfig, spec: CommentSpec,
            post: SourceMessage) -> str:
    tone = compose("ai_comment_tone_positive" if spec.tone == POSITIVE
                   else "ai_comment_tone_neutral", settings=ctx.settings).strip()
    extra = config.extra_prompts[0] if config.extra_prompts else "-"
    return compose("ai_comment", settings=ctx.settings,
                   post=post.text or "(no text)", tone=tone, extra=extra,
                   max_chars=config.max_chars)


def _clip(text_value: str, limit: int) -> str:
    body = " ".join(text_value.split())
    return body if len(body) <= limit else body[:limit].rstrip()


def capture_posts(sender: object, account: str, targets: list[str],
                  limit: int) -> list[SourceMessage]:
    """Read each target's recent posts live, keeping their real ids.

    A comment is a reply, so the id is the whole point: the offline stand-ins
    are numbered 1..N and a real run that used them would be replying to
    whatever happens to sit at those ids.  ``history`` already drops a post
    with no text, so a service message never becomes a comment target.
    """
    captured: list[SourceMessage] = []
    for target in targets:
        parsed = parse_many([target], "custom")[0]
        result = sender.history(account, parsed, minutes=0,  # type: ignore[attr-defined]
                                limit=limit)
        if not result.ok:
            print(f"target={target} capture failed: {result.code}")
            continue
        for post_id, body in result.posts:
            captured.append(SourceMessage(peer=target, sender="", text=body,
                                          message_id=post_id))
        print(f"target={target} captured {len(result.posts)}/{result.count} "
              f"limit={limit}")
    return captured


def run(config: CommentConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts]
    if ctx.live and not config.messages and accounts:
        captured = capture_posts(ctx.sender(), labels[0], config.targets,
                                 config.poll_recent or config.posts)
        if captured:
            config = replace(config, messages=captured)
        else:
            print("warning: no post was captured, the run has nothing to "
                  "comment on")
    stats = plan(config, labels)

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        targets=len(config.targets),
        interval=f"{config.comment_interval_min:.0f}-"
                 f"{config.comment_interval_max:.0f}s",
        per_post=f"{config.per_post_min}-{config.per_post_max}",
        positive_ratio=f"{config.positive_ratio}%",
        likes=f"{config.likes_min}-{config.likes_max}",
        emoji_probability=f"{config.emoji_probability}%",
        max_chars=config.max_chars,
        poll_recent=config.poll_recent,
        queue=stats.total,
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"targets": config.targets}, stats.total)
    sender = ctx.sender()
    ai = ai_client(ctx)
    posts = {target: {post.message_id: post
                      for post in posts_for(config, target)}
             for target in config.targets}
    ok = failed = 0
    waited = 0.0
    ai_fallback = 0
    emoji_used = 0
    reactions = 0

    for index, spec in enumerate(stats.specs, start=1):
        target: Target = parse_many([spec.target], "custom")[0]
        post = posts[spec.target].get(spec.post_id) or SourceMessage(
            peer=spec.target, sender="", text="")
        generated = ai.generate(_prompt(ctx, config, spec, post),
                                fallback=post.text)
        record_ai(ctx, task, spec.account, "comment", generated)
        if not generated.ok:
            ai_fallback += 1
        pause(ctx, spec.delay)
        body = _clip(generated.response, config.max_chars)
        if spec.emoji:
            emoji_used += 1
            body = f"{body} {random.choice(BODY_EMOJI)}".strip()
        outcome = sender.send(spec.account, target,
                              Content(text=body, reply_to=spec.post_id))
        waited += spec.delay
        if outcome.ok:
            ok += 1
            reacted = 0
            for like in range(max(0, spec.likes)):
                if like >= len(labels):
                    break  # an account may leave only one reaction per post
                one = sender.react(labels[like], target, spec.post_id,
                                   config.like_emoji or DEFAULT_REACTION)
                if not one.ok:
                    print(f"reaction on {spec.post_id} from {labels[like]} "
                          f"-> {one.code}")
                    continue
                reacted += 1
            reactions += reacted
            record(ctx, task, spec.account, f"{target.display()}#{spec.post_id}",
                   True, detail=f"likes={spec.likes} reactions={reacted}")
            state = f"ok reactions={reacted}/{spec.likes}"
        else:
            failed += 1
            record(ctx, task, spec.account, f"{target.display()}#{spec.post_id}",
                   False, outcome.code)
            state = outcome.code
        print(f"[{index:03d}/{stats.total:03d}] account={spec.account} "
              f"target={target.display()} post={spec.post_id} "
              f"tone={spec.tone} likes={spec.likes} "
              f"emoji={'yes' if spec.emoji else 'no'} "
              f"delay={spec.delay:.0f}s text={body[:40]} -> {state}")

    print(f"Processed: {ok + failed} / {stats.total}")
    print(f"ok: {ok}")
    print(f"failed: {failed}")
    print(f"positive: {stats.positive} ({stats.ratio(stats.positive):.0f}%)")
    print(f"emoji: {emoji_used} ({stats.ratio(emoji_used):.0f}%)")
    print(f"reactions: {reactions}")
    print(f"ai calls: {len(ai.calls)} (fallback {ai_fallback})")
    print(f"waited: {format_duration(waited)}")
    print(f"PLAN: {stats.total} comments, {max(1, len(labels))} accounts, "
          f"ETA {format_duration(waited)}")
    close_task(ctx, task, ok=ok, failed=failed)
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(CommentConfig.from_dict(raw, ctx.settings), ctx)


__all__ = [
    "CommentConfig",
    "CommentSpec",
    "NEUTRAL",
    "POSITIVE",
    "PlanStats",
    "TITLE",
    "main",
    "plan",
    "posts_for",
    "run",
]
