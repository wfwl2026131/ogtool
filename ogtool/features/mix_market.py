"""#20 auto marketing - five fixed stages, one after another.

The page is a workflow runner, not a new kind of job: it chains the modules
that already exist.

    1) add contacts      2) create your own groups      3) play the script
    4) invite your contacts into those groups            5) message them

Everything a stage needs is a field on this page; ``--stop-after`` cuts the run
after one stage so a single step can be checked on its own.  Two things the page
shows are simulated here as well: the dead chat check (a chat whose last message
is older than the threshold is dropped from the later stages) and the rest the
runner takes after a group could not be created.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .. import chat_pattern
from ..config import ConfigError, Settings
from ..flags import Flag
from ..limiter import format_duration
from ..runtime import RunContext
from ..senders import Content
from ..store import Account
from ..targets import parse_many
from . import add_contact, batch_invite_v2, mass_send_v2, register
from ._common import (
    accounts_of,
    close_task,
    header,
    name_of,
    num,
    open_task,
    record,
    section,
    text,
)
from .puppet_chat import PuppetConfig, play, resolve_pattern

FEATURE = "mix_market"
SECTION = "mix_market"
TITLE = "#20 自动营销"

STAGES = (
    ("add_contact", "add contacts"),
    ("create_group", "create groups"),
    ("puppet_chat_v2", "play the script"),
    ("batch_invite_v2", "invite own contacts"),
    ("mass_send_v2", "message own contacts"),
)

FLAGS = (
    Flag("--group-title", "group_title", str,
         "a group to create (repeatable)", action="append"),
    Flag("--targets", "targets", str, "existing groups, comma separated"),
    Flag("--pattern", "pattern", str, "script file (话术文件)"),
    Flag("--text", "text", str, "message text for the last stage"),
    Flag("--media", "media", str, "message media for the last stage"),
    Flag("--contact", "contact", str,
         "contact: name|ref (repeatable)", action="append"),
    Flag("--dead-check-interval", "dead_check_interval", float,
         "seconds between two dead chat checks"),
    Flag("--dead-after-hours", "dead_after_hours", float,
         "a chat silent for this long counts as dead"),
    Flag("--activity", "activity", str,
         "simulated activity: ref=hours (repeatable)", action="append"),
    Flag("--create-rest-min", "create_rest_min", float,
         "minutes to rest after a failed create (minimum)"),
    Flag("--create-rest-max", "create_rest_max", float,
         "minutes to rest after a failed create (maximum)"),
    Flag("--stop-after", "stop_after", int, "stop after stage 1..5"),
)


@dataclass(slots=True)
class MixConfig:
    account_labels: list[str] = field(default_factory=list)
    group_titles: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    pattern: str = ""
    text: str = ""
    media: str = ""
    contacts: list[str] = field(default_factory=list)
    dead_check_interval: float = 30.0
    dead_after_hours: float = 72.0
    activity: dict[str, float] = field(default_factory=dict)
    create_rest_min: float = 1.0
    create_rest_max: float = 5.0
    stop_after: int = len(STAGES)
    task_name: str = "mix-market"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> MixConfig:
        del settings
        data = section(raw, SECTION)
        titles = _unique(_as_list(data.get("group_title")))
        targets = [item.strip() for item in
                   str(data.get("targets") or "").replace("\n", ",").split(",")
                   if item.strip()]
        if not titles and not targets:
            raise ConfigError(
                f"{SECTION}: nothing to work on (--group-title or --targets)"
            )
        return cls(
            account_labels=accounts_of(raw, data),
            group_titles=titles,
            targets=targets,
            pattern=text(data, "pattern"),
            text=text(data, "text"),
            media=text(data, "media"),
            contacts=_unique(_as_list(data.get("contact"))),
            dead_check_interval=num(data, "dead_check_interval", 30.0),
            dead_after_hours=num(data, "dead_after_hours", 72.0),
            activity=_activity(_as_list(data.get("activity"))),
            create_rest_min=num(data, "create_rest_min", 1.0),
            create_rest_max=num(data, "create_rest_max", 5.0),
            stop_after=num(data, "stop_after", len(STAGES), kind=int,
                          minimum=1),
            task_name=name_of(raw, data, "mix-market"),
        )


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return [part.strip() for part in str(value).replace(",", "\n").splitlines()
            if part.strip()]


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _activity(values: list[str]) -> dict[str, float]:
    """``ref=hours`` pairs, so a dry run can decide which chats are dead."""
    out: dict[str, float] = {}
    for entry in values:
        ref, _, hours = str(entry).partition("=")
        if not ref.strip() or not hours.strip():
            raise ConfigError(f"--activity needs ref=hours, got {entry!r}")
        try:
            out[ref.strip()] = float(hours)
        except ValueError as error:
            raise ConfigError(
                f"--activity hours is not a number: {entry!r}"
            ) from error
    return out


def slug(title: str) -> str:
    """A channel title turned into something ``Target`` can parse."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return f"@og_{cleaned[:24] or 'group'}"


@dataclass(slots=True)
class StageResult:
    name: str
    label: str
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    seconds: float = 0.0
    rested: float = 0.0

    def line(self, index: int, total: int) -> str:
        return (f"stage {index}/{total} {self.name}: ok={self.ok} "
                f"failed={self.failed} skipped={self.skipped} "
                f"{self.seconds:.2f}s")


def create_refs(config: MixConfig) -> list[str]:
    """Where the groups of stage 2 will live: a slug per title."""
    return [slug(title) for title in config.group_titles]


def dead_chats(config: MixConfig, refs: list[str]) -> list[str]:
    """Refs whose last message is older than the threshold."""
    dead: list[str] = []
    for ref in refs:
        hours = config.activity.get(ref)
        if hours is None:
            continue
        if hours >= config.dead_after_hours:
            dead.append(ref)
    return dead


def rest_after_failure(config: MixConfig, index: int) -> float:
    """Minutes to rest after the index-th failed create, as the page allows."""
    span = max(0.0, config.create_rest_max - config.create_rest_min)
    return config.create_rest_min + (span * min(index, 3) / 3.0)


def stage_add_contacts(config: MixConfig, ctx: RunContext,
                       labels: list[str]) -> tuple[StageResult, dict[str, str]]:
    """Add the contacts; returns who added whom, for the last stage."""
    result = StageResult("add_contact", STAGES[0][1])
    owners: dict[str, str] = {}
    contacts = [entry.split("|", 1)[-1] for entry in config.contacts]
    jobs = add_contact.plan_jobs(contacts, labels or ["-"])
    sender = ctx.sender()
    for account, contact, _round in jobs:
        target = parse_many([contact], "custom")[0]
        answer = sender.add_contact(account, target)
        if answer.ok:
            result.ok += 1
            owners[contact] = account
        else:
            result.failed += 1
    return result, owners


def stage_create_groups(config: MixConfig, ctx: RunContext,
                        labels: list[str]) -> StageResult:
    result = StageResult("create_group", STAGES[1][1])
    sender = ctx.sender()
    account = labels[0] if labels else "-"
    for title in config.group_titles:
        answer = sender.create_group(account, title)
        if answer.ok:
            result.ok += 1
            continue
        result.failed += 1
        minutes = rest_after_failure(config, result.failed)
        result.rested += minutes * 60.0
        until = datetime.now(UTC).replace(microsecond=0) + timedelta(
            minutes=minutes)
        print(f"rest: create failed at {title!r} ({answer.code}), "
              f"waiting {minutes:.0f} min until {until:%H:%M:%S}")
    return result


def stage_play(config: MixConfig, ctx: RunContext, refs: list[str],
               labels: list[str]) -> StageResult:
    result = StageResult("puppet_chat_v2", STAGES[2][1])
    if not config.pattern:
        print("skip: stage 3 needs --pattern")
        result.skipped = len(refs)
        return result
    base = Path(ctx.settings.data_dir)
    path = resolve_pattern(config.pattern, "", "", base)
    messages = chat_pattern.load(path)
    playback = PuppetConfig(
        account_labels=config.account_labels, targets=list(refs),
        pattern=config.pattern, mode_file="", mode="",
        loop_interval=0.0, loops=1, multiplier=1, daily_limit=0,
        polish=0, ai_polish=False, pictures="", task_name=config.task_name,
    )
    task = open_task(ctx, f"{config.task_name}:3", FEATURE, {"stage": 3},
                     len(refs) * len(messages))
    outcome = play(ctx, task, messages, playback, labels)
    result.ok = outcome.ok
    result.failed = outcome.failed
    result.skipped = outcome.skipped
    return result


def stage_invite(config: MixConfig, ctx: RunContext, refs: list[str],
                 labels: list[str]) -> StageResult:
    result = StageResult("batch_invite_v2", STAGES[3][1])
    contacts = [entry.split("|", 1)[-1] for entry in config.contacts]
    jobs = batch_invite_v2.build_jobs(refs, contacts, labels or ["-"])
    sender = ctx.sender()
    for account, group, contact in jobs:
        answer = sender.invite(account, parse_many([group], "group")[0],
                               parse_many([contact], "custom")[0])
        if answer.ok:
            result.ok += 1
        else:
            result.failed += 1
    return result


def stage_send(config: MixConfig, ctx: RunContext, labels: list[str],
               owners: dict[str, str] | None = None) -> StageResult:
    result = StageResult("mass_send_v2", STAGES[4][1])
    book: dict[str, list[mass_send_v2.Contact]] = {}
    for entry in config.contacts:
        name, _, ref = entry.partition("|")
        owner = ((owners or {}).get(ref or name)
                 or (labels[0] if labels else "-"))
        book.setdefault(owner, []).append(
            mass_send_v2.Contact(owner, ref or name, name))
    if not config.text and not config.media:
        print("skip: stage 5 needs --text or --media")
        result.skipped = len(book)
        return result
    flat, _dropped = mass_send_v2.dedupe_contacts(book, False)
    sender = ctx.sender()
    content = Content(text=config.text, media=config.media)
    for contact in flat:
        answer = sender.send(contact.account,
                             parse_many([contact.ref], "custom")[0], content)
        if answer.ok:
            result.ok += 1
        else:
            result.failed += 1
    return result


def run(config: MixConfig, ctx: RunContext) -> int:
    accounts: list[Account] = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts]
    refs = create_refs(config) + config.targets

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        stages=f"{config.stop_after}/{len(STAGES)}",
        groups_to_create=len(config.group_titles),
        existing_targets=len(config.targets),
        contacts=len(config.contacts),
        dead_check=f"every {config.dead_check_interval:.0f}s, "
                   f"dead after {config.dead_after_hours:.0f}h",
        create_rest=f"{config.create_rest_min:.0f}-"
                    f"{config.create_rest_max:.0f} min",
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"stages": config.stop_after}, len(config.contacts))
    results: list[StageResult] = []
    waited = 0.0

    def run_stage(index: int, function, *arguments) -> None:
        if index > config.stop_after:
            return
        started = time.perf_counter()
        outcome = function(*arguments)
        outcome.seconds = time.perf_counter() - started
        nonlocal waited
        waited += outcome.rested
        results.append(outcome)
        print(outcome.line(index, len(STAGES)))

    owners: dict[str, str] = {}
    if config.stop_after >= 1:
        started = time.perf_counter()
        contacts_stage, owners = stage_add_contacts(config, ctx, labels)
        contacts_stage.seconds = time.perf_counter() - started
        results.append(contacts_stage)
        print(contacts_stage.line(1, len(STAGES)))
    run_stage(2, stage_create_groups, config, ctx, labels)

    dead = dead_chats(config, refs)
    if refs:
        waited += config.dead_check_interval * len(refs)
        for ref in refs:
            hours = config.activity.get(ref)
            if ref in dead:
                print(f"dead check: target={ref} hours={hours:.0f} -> dead "
                      f"(interval {config.dead_check_interval:.0f}s)")
                record(ctx, task, labels[0] if labels else "-", ref, False,
                       "DEAD_CHAT")
            else:
                seen = "unknown" if hours is None else f"{hours:.0f}"
                print(f"dead check: target={ref} hours={seen} -> alive")
    live_refs = [ref for ref in refs if ref not in dead]

    run_stage(3, stage_play, config, ctx, live_refs, labels)
    run_stage(4, stage_invite, config, ctx, live_refs, labels)
    run_stage(5, stage_send, config, ctx, labels, owners)

    ok = sum(item.ok for item in results)
    failed = sum(item.failed for item in results)
    skipped = sum(item.skipped for item in results)
    print(f"Processed: {len(results)} / {config.stop_after}")
    print(f"ok: {ok}")
    print(f"failed: {failed}")
    print(f"skipped: {skipped}")
    print(f"dead chats: {len(dead)}")
    print(f"PLAN: {len(results)} stages, {len(config.contacts)} contacts, "
          f"ETA {format_duration(waited)}")
    close_task(ctx, task, ok=ok, failed=failed)
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(MixConfig.from_dict(raw, ctx.settings), ctx)


__all__ = [
    "MixConfig",
    "STAGES",
    "StageResult",
    "TITLE",
    "create_refs",
    "dead_chats",
    "main",
    "rest_after_failure",
    "run",
    "slug",
]
