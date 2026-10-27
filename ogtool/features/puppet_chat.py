"""#0 puppet chat - replay a chat_pattern script across the target chats.

The page is the player for the script format: a file of lines, an account pool
that fills the ``A<n>`` slots, a loop interval in minutes and a repeat count.
``message multiplier`` repeats every line, ``per account daily cap`` caps one account per day
and makes the slot rebind to another account when it is reached, and the polish
switch rewrites the wording through the model without touching the structure.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .. import chat_pattern
from ..chat_pattern import ChatMessage, PatternError
from ..config import ConfigError, Settings
from ..flags import Flag
from ..limiter import PeerFloodBackoff, format_duration
from ..prompts import compose
from ..report import read_lines
from ..runtime import RunContext
from ..senders import Content
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

FEATURE = "puppet_chat"
SECTION = "puppet_chat"
TITLE = "#0 话术炒群"

FLAGS = (
    Flag("--targets", "targets", str, "targets, comma separated"),
    Flag("--targets-file", "files", str, "file with one target per line",
         action="append"),
    Flag("--pattern", "pattern", str, "script file (话术文件)"),
    Flag("--mode-file", "mode_file", str, "mode file (模式文件), optional"),
    Flag("--mode", "mode", str, "mode name inside the mode file"),
    Flag("--loop-interval", "loop_interval", float,
         "minutes between two loops"),
    Flag("--loops", "loops", int, "how many times the script plays"),
    Flag("--multiplier", "multiplier", int, "message multiplier"),
    Flag("--daily-limit", "daily_limit", int,
         "text messages one account may send per day"),
    Flag("--polish", "polish", int, "polish level 0-3"),
    Flag("--ai-polish", "ai_polish", None, "polish through the model",
         action="store_true"),
)


def parse_modes(path: str | Path) -> list[tuple[str, str]]:
    """A mode file: ``name=path`` lines, or one bare script path per line."""
    pairs: list[tuple[str, str]] = []
    for line in read_lines(path):
        if "=" in line:
            name, target = line.split("=", 1)
            pairs.append((name.strip(), target.strip()))
        else:
            pairs.append((Path(line).stem, line.strip()))
    return pairs


def resolve_pattern(pattern: str, mode_file: str, mode: str,
                    base: Path) -> Path:
    """The script to play: the mode file's pick, else the script itself."""
    chosen = pattern
    if mode_file:
        modes = parse_modes(mode_file)
        if not modes:
            raise ConfigError(f"mode file has no entry: {mode_file}")
        if mode:
            matches = [target for name, target in modes if name == mode]
            if not matches:
                known = ", ".join(name for name, _ in modes)
                raise ConfigError(
                    f"mode {mode!r} is not in {mode_file} (have: {known})"
                )
            chosen = matches[0]
        else:
            chosen = modes[0][1]
    if not chosen:
        raise ConfigError(f"{SECTION}: no script given (--pattern)")
    path = Path(chosen).expanduser()
    if not path.is_absolute():
        candidates = [base / path, base / "tdata" / "chat_pattern" / path,
                      Path.cwd() / path]
        path = next((item for item in candidates if item.exists()),
                    candidates[0])
    if not path.exists():
        raise PatternError(f"script not found: {path}")
    return path


@dataclass(slots=True)
class PuppetConfig:
    account_labels: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    #: The targets as they parsed, kind included. ``targets`` alone loses the
    #: "this id is a channel" fact: a ``t.me/c/<id>`` link parses to kind
    #: channel, its bare ref re-parses as a group-with-phone-fallback, and a
    #: second account without the chat cached used to die on that detour.
    target_specs: list[Target] = field(default_factory=list)
    pattern: str = ""
    mode_file: str = ""
    mode: str = ""
    loop_interval: float = 5.0
    loops: int = 1
    multiplier: int = 1
    daily_limit: int = 0
    polish: int = 0
    ai_polish: bool = False
    pictures: str = ""
    #: ``scheduleDateTime``: wait for this wall clock time before line one.
    schedule_at: str = ""
    #: ``disableLinkPreview``: send the line without a link preview.
    disable_link_preview: bool = False
    task_name: str = "puppet-chat"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> PuppetConfig:
        data = section(raw, SECTION)
        specs = targets_of(data)
        targets = [target.ref for target in specs]
        if not targets:
            raise ConfigError(f"{SECTION}: no target given (--targets)")
        if not text(data, "pattern") and not text(data, "mode_file"):
            raise ConfigError(f"{SECTION}: no script or mode file given")
        pictures = text(data, "pictures") or str(
            chat_pattern.default_picture_dir(settings.data_dir)
        )
        return cls(
            account_labels=accounts_of(raw, data),
            targets=targets,
            target_specs=specs,
            pattern=text(data, "pattern"),
            mode_file=text(data, "mode_file"),
            mode=text(data, "mode"),
            loop_interval=num(data, "loop_interval", 5.0),
            loops=num(data, "loops", 1, kind=int, minimum=1),
            multiplier=num(data, "multiplier", 1, kind=int, minimum=1),
            daily_limit=num(data, "daily_limit", 0, kind=int, minimum=0),
            polish=num(data, "polish", 0, kind=int, minimum=0),
            ai_polish=bool(data.get("ai_polish", False)),
            pictures=pictures,
            schedule_at=text(data, "schedule_at"),
            disable_link_preview=bool(data.get("disable_link_preview")),
            task_name=name_of(raw, data, "puppet-chat"),
        )


#: How often one line is retried after PEER_FLOOD (the page has no field).
FLOOD_CAP = 2


@dataclass(slots=True)
class PlayResult:
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    waited: float = 0.0

    @property
    def processed(self) -> int:
        return self.ok + self.failed + self.skipped


def _content(message: ChatMessage, pictures: Path,
             disable_preview: bool = False) -> Content:
    """One script line -> the payload the sender takes."""
    image = str(pictures / message.image) if message.image else ""
    return Content(
        text=message.text,
        media=image,
        sticker=message.sticker.raw if message.sticker else "",
        # The page's switch turns the preview off; it is carried on the payload
        # because that is the only place the transport can see it.
        no_preview=disable_preview,
    )


def play(ctx: RunContext, task, messages: list[ChatMessage],
         config: PuppetConfig, labels: list[str], *,
         startup_offsets: dict[str, float] | None = None,
         ai: object | None = None) -> PlayResult:
    """Replay the script over every target; returns what the page counts."""
    slots = chat_pattern.assign_slots(messages, labels)
    sender = ctx.sender()
    backoff = PeerFloodBackoff()
    pictures = Path(config.pictures)
    rng = random.Random()
    result = PlayResult()
    budget: dict[str, int] = {}
    # The startup stagger is a startup cost: each account pays its offset
    # once, on its first message of the run. Adding it to every message
    # turned "account B starts 10s later" into "every message from B waits
    # 10s longer" - pacing by accident.
    started: set[str] = set()

    wait, when = schedule_wait(config, datetime.now(UTC))
    if wait:
        print(f"schedule: {when} (waiting {format_duration(wait)})")
        pause(ctx, wait)

    total = len(config.targets) * len(messages) * config.loops * config.multiplier
    index = 0
    for target in (config.target_specs
                   or parse_many(config.targets, "custom")):
        for loop in range(config.loops):
            if loop:
                result.waited += config.loop_interval * 60.0
                if not ctx.live:
                    print(f"  loop {loop + 1}/{config.loops} after "
                          f"{config.loop_interval:.0f} min")
                else:
                    time.sleep(config.loop_interval * 60.0)
            for message in chat_pattern.multiply(messages, config.multiplier):
                index += 1
                account = slots.get(message.account_slot, labels[0] if labels
                                    else "-")
                account = _rebind(account, labels, budget, config.daily_limit,
                                  message.account_slot)
                if account is None:
                    result.skipped += 1
                    print(f"skipped: no account under the daily limit "
                          f"({config.daily_limit}) for A"
                          f"{message.account_slot}")
                    continue
                delay = message.delay(rng)
                if startup_offsets and account not in started:
                    delay += startup_offsets.get(account, 0.0)
                    started.add(account)
                result.waited += delay
                if ctx.live:
                    time.sleep(delay)

                body = _content(message, pictures,
                                config.disable_link_preview)
                if config.ai_polish and config.polish > 0 and body.text:
                    body = _polish(ctx, task, ai, account, body, config.polish)
                floods = 0
                while True:
                    outcome = sender.send(account, target, body)
                    if outcome.code != "PEER_FLOOD" or floods >= FLOOD_CAP:
                        break
                    floods += 1
                    wait = backoff.wait_for(floods - 1)
                    result.waited += wait
                    print(f"  PEER_FLOOD backoff {wait:.0f}s "
                          f"(attempt {floods}/{FLOOD_CAP}) A"
                          f"{message.account_slot}")
                    if ctx.live:
                        time.sleep(wait)
                if outcome.ok:
                    result.ok += 1
                    budget[account] = budget.get(account, 0) + 1
                    record(ctx, task, account, target.display(), True)
                    state = "ok"
                else:
                    result.failed += 1
                    record(ctx, task, account, target.display(), False,
                           outcome.code, outcome.detail)
                    state = outcome.code
                print(f"[{index:03d}/{total:03d}] A{message.account_slot} "
                      f"account={account} target={target.display()} "
                      f"delay={delay:.0f}s {_describe(body)} -> {state}")
    return result


def _rebind(account: str, labels: list[str], budget: dict[str, int],
            limit: int, slot: int) -> str | None:
    """Move a slot to an account that still has budget, or stop the line."""
    if not limit or not labels:
        return account
    if budget.get(account, 0) < limit:
        return account
    for candidate in labels:
        if budget.get(candidate, 0) < limit:
            print(f"  daily limit: account={account} count="
                  f"{budget.get(account, 0)} limit={limit}, "
                  f"reassign puppet A{slot} -> {candidate}")
            return candidate
    print(f"  daily limit: account={account} count={budget.get(account, 0)} "
          f"limit={limit}, no free account for A{slot}")
    return None


def _polish(ctx: RunContext, task, ai: object | None, account: str,
            body: Content, level: int) -> Content:
    """Rewrite the wording, structure untouched; a failure keeps the line."""
    if ai is None:
        ai = ai_client(ctx)
    prompt = compose("ai_polish", settings=ctx.settings,
                     level=level, text=body.text)
    outcome = ai.generate(prompt, fallback=body.text)  # type: ignore[attr-defined]
    record_ai(ctx, task, account, "polish", outcome)
    if not outcome.ok:
        return body
    return Content(text=outcome.response, media=body.media,
                   sticker=body.sticker)


def _describe(body: Content) -> str:
    parts = []
    if body.text:
        parts.append(f"text={body.text}")
    if body.media:
        parts.append(f"img={Path(body.media).name}")
    if body.sticker:
        parts.append(f"sticker={body.sticker}")
    return " ".join(parts) or "text=(empty)"


def schedule_wait(config: PuppetConfig, now: datetime) -> tuple[float, str]:
    """How long ``scheduleDateTime`` holds the run, and what to say about it.

    ``(0.0, "now")`` when no time is set. A time already in the past is
    reported and waited out with zero: the script runs immediately rather than
    pretending it fired on schedule.
    """
    if not config.schedule_at:
        return 0.0, "now"
    try:
        at = datetime.fromisoformat(config.schedule_at).replace(tzinfo=UTC)
    except ValueError as error:
        raise ConfigError(
            f"{SECTION}: schedule_at is not an ISO time: "
            f"{config.schedule_at}") from error
    delta = (at - now).total_seconds()
    if delta <= 0:
        return 0.0, f"{config.schedule_at} (past, starting now)"
    return delta, config.schedule_at


def run(config: PuppetConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts]
    base = Path(ctx.settings.data_dir)
    path = resolve_pattern(config.pattern, config.mode_file, config.mode, base)
    messages = chat_pattern.load(path)
    slots = chat_pattern.assign_slots(messages, labels)

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        targets=len(config.targets),
        script=path.name,
        lines=len(messages),
        slots=", ".join(f"A{slot}->{who}" for slot, who in sorted(slots.items())),
        loop_interval=f"{config.loop_interval:.0f} min",
        loops=config.loops,
        multiplier=config.multiplier,
        daily_limit=config.daily_limit or "-",
        polish=f"{config.polish}{' + ai' if config.ai_polish else ''}",
        schedule=config.schedule_at or "now",
        preview="off" if config.disable_link_preview else "on",
    )

    total = len(config.targets) * len(messages) * config.loops * config.multiplier
    task = open_task(ctx, config.task_name, FEATURE,
                     {"script": str(path), "targets": config.targets}, total)
    ai = ai_client(ctx) if config.ai_polish and config.polish else None
    outcome = play(ctx, task, messages, config, labels, ai=ai)

    print(f"Processed: {outcome.processed} / {total}")
    print(f"ok: {outcome.ok}")
    print(f"failed: {outcome.failed}")
    print(f"skipped: {outcome.skipped}")
    print(f"waited: {format_duration(outcome.waited)}")
    print(f"PLAN: {total} messages, {max(1, len(labels))} accounts, "
          f"ETA {format_duration(outcome.waited)}")
    close_task(ctx, task, ok=outcome.ok, failed=outcome.failed)
    return 0 if not outcome.failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(PuppetConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["PuppetConfig", "PlayResult", "TITLE", "main", "parse_modes",
           "play", "resolve_pattern", "run"]
