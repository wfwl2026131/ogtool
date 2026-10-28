"""#5 mass send.

Targets (private / group / client / custom), a content source (text / media /
forward), the interval numbers, threads and the three caps the reference page
shows.  The page also carries five switches, and they are all here:

``immediateStart`` / ``scheduledDateTime``
    start now, or wait for a wall clock time that is given in the task file;
``loadBalance``
    hand each target to the account with the most budget left, instead of
    round robin;
``taskMark``
    append the task's own marker to the text, so a sent message can be traced
    back to the task that produced it;
``removeSentCustomTargets``
    after the plan, drop the targets it covered from the custom list file;
``lastMediaDir``
    the folder a relative media path is resolved against, the way the page
    remembers the last media picker location.

A dry run walks the whole pipeline and prints every message it would send;
only the transport is replaced.
"""

from __future__ import annotations

import random
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..ai import AIClient
from ..config import Settings
from ..content import ContentSpec
from ..content import build as build_content
from ..limiter import Budget, Limits, PeerFloodBackoff, format_duration
from ..runtime import RunContext
from ..senders import PEER_FLOOD, SENDING_FEATURES, Content
from ..store import Account
from ..targets import Target, collect
from . import register
from ._common import close_task, lanes_by, open_task, pause

FEATURE = "mass_send"


@dataclass(slots=True)
class MassSendConfig:
    """Everything the right pane of #5 carries, with the page's own names."""

    account_labels: list[str] = field(default_factory=list)
    target_kind: str = "custom"
    targets: list[Target] = field(default_factory=list)
    content: ContentSpec = field(default_factory=ContentSpec)
    limits: Limits = field(default_factory=Limits)
    #: ``immediate`` starts now; ``scheduled`` waits for ``schedule_at``.
    start_mode: str = "immediate"
    schedule_at: str = ""
    load_balance: bool = False
    task_mark_enabled: bool = True
    task_mark: str = ""
    remove_sent_custom_targets: bool = False
    custom_targets_file: str = ""
    last_media_dir: str = ""
    task_name: str = "mass-send"
    dry_run: bool = True

    @classmethod
    def from_dict(
        cls,
        raw: dict,
        settings: Settings,
        *,
        dry_run: bool = True,
    ) -> MassSendConfig:
        send = raw.get("send") or {}
        targets_raw = raw.get("targets") or {}
        content_raw = raw.get("content") or {}

        kind = str(targets_raw.get("kind", "custom"))
        resolved = collect(
            inline=targets_raw.get("inline"),
            files=targets_raw.get("files") or [],
            kind=kind,
        )
        targets = resolved

        limits = Limits(
            account_min=float(send.get("account_interval_min", 5)),
            account_max=float(send.get("account_interval_max", 12)),
            group_interval=float(send.get("group_interval", 3)),
            threads=int(send.get("threads", 1)),
            daily_limit=int(send.get("daily_limit", 50)),
            success_cap=int(send.get("success_cap", 0)),
            exec_cap=int(send.get("exec_cap", 0)),
        )
        return cls(
            account_labels=list(raw.get("accounts") or []),
            target_kind=kind,
            targets=targets,
            content=ContentSpec(
                text=str(content_raw.get("text", "")),
                media=str(content_raw.get("media", "")),
                forward_from=str(content_raw.get("forward_from", "")),
                prompt=str(content_raw.get("prompt", "")),
                ai=bool(content_raw.get("ai", False)),
            ),
            limits=limits,
            start_mode=str(send.get("start_mode") or "immediate").lower(),
            schedule_at=str(send.get("schedule_at") or ""),
            load_balance=bool(send.get("load_balance")),
            task_mark_enabled=bool(send.get("task_mark_enabled", True)),
            task_mark=str(send.get("task_mark") or ""),
            remove_sent_custom_targets=bool(
                send.get("remove_sent_custom_targets")),
            custom_targets_file=str(send.get("custom_targets_file") or ""),
            last_media_dir=str(send.get("last_media_dir") or ""),
            task_name=str(raw.get("task_name") or "mass-send"),
            dry_run=dry_run,
        )


@dataclass(slots=True)
class Planned:
    index: int
    offset: float
    account: str
    target: Target


def _interleave(accounts: list[str], targets: list[Target],
                limits: Limits, rng: random.Random) -> list[tuple[str, Target]]:
    """Round robin accounts over targets, one target at a time."""
    pairs: list[tuple[str, Target]] = []
    if not accounts or not targets:
        return pairs
    for position, target in enumerate(targets):
        account = accounts[position % len(accounts)]
        pairs.append((account, target))
        if limits.exec_cap and len(pairs) >= limits.exec_cap:
            break
    return pairs


def schedule(
    pairs: list[tuple[str, Target]],
    limits: Limits,
    *,
    accounts: list[str],
    rng: random.Random | None = None,
    budgets: dict[str, Budget] | None = None,
) -> list[Planned]:
    """Give every pair a send time, honouring the per account and per target gaps.

    Each account is a lane with its own clock; a message waits for both the
    lane clock and the target's own gap.  ``success_cap`` stops adding entries,
    which is what the reference page's success cap does.
    """
    rng = rng or random.Random(0)
    clocks: dict[str, float] = {account: 0.0 for account in accounts}
    target_clock: dict[str, float] = {}

    planned: list[Planned] = []
    for index, (account, target) in enumerate(pairs, start=1):
        if limits.success_cap and len(planned) >= limits.success_cap:
            break
        budget = (budgets or {}).get(account)
        if budget is not None and not budget.take():
            continue

        lane = clocks.get(account, 0.0)
        gap = rng.uniform(limits.account_min, limits.account_max)
        at = max(lane, target_clock.get(target.ref, 0.0))
        planned.append(Planned(index, at, account, target))
        clocks[account] = at + gap
        target_clock[target.ref] = at + limits.group_interval
    return planned


def plan_times(planned: list[Planned], start: datetime) -> list[datetime]:
    return [start + timedelta(seconds=item.offset) for item in planned]


def render(
    planned: list[Planned],
    contents: dict[str, Content],
    start: datetime,
) -> list[str]:
    """One line per message: index, time, account, target, content."""
    lines: list[str] = []
    for item in planned:
        moment = start + timedelta(seconds=item.offset)
        content = contents[item.account]
        lines.append(
            f"[{item.index:03d}] {moment:%Y-%m-%d %H:%M:%S} "
            f"account={item.account} target={item.target.display()} "
            f"content={content.describe()}"
        )
    return lines


def _budgets(raw_accounts: list[Account], limits: Limits,
             ctx: RunContext) -> dict[str, Budget]:
    budgets: dict[str, Budget] = {}
    for account in raw_accounts:
        # Only pages that send messages may spend this budget: counting every
        # successful row (exports, heartbeats) meant the day's allowance was
        # already gone before the first message.
        used = ctx.store.count_sent_today(account.label,
                                          features=SENDING_FEATURES)
        budgets[account.label] = Budget(limits.daily_limit, used)
    return budgets


def _start_time(config: MassSendConfig, now: datetime) -> tuple[datetime, str]:
    """When the first message goes out, and why.

    ``scheduled`` with no time is a config mistake the page cannot show, so it
    is reported instead of quietly turning into "now"; a time already in the
    past is reported too and the run starts immediately rather than pretending
    it fired on time.
    """
    if config.start_mode not in ("immediate", "scheduled"):
        raise ValueError(f"start_mode must be immediate or scheduled, "
                         f"is {config.start_mode}")
    if config.start_mode == "immediate" or not config.schedule_at:
        return now, "immediate"
    try:
        parsed = datetime.fromisoformat(config.schedule_at).replace(tzinfo=UTC)
    except ValueError as error:
        raise ValueError(
            f"schedule_at is not an ISO time: {config.schedule_at}"
        ) from error
    if parsed <= now:
        print(f"WARN: scheduled time {parsed:%Y-%m-%d %H:%M:%S} is in the past; "
              f"starting now")
        return now, "scheduled (late)"
    return parsed, "scheduled"


def _balanced(accounts: list[str], targets: list[Target], limits: Limits,
              budgets: dict[str, Budget]) -> list[tuple[str, Target]]:
    """Hand each target to the account with the most budget left.

    Ties go to the account that was used least so far, which keeps the plan
    from stacking on account 1 when every budget is untouched.
    """
    pairs: list[tuple[str, Target]] = []
    if not accounts or not targets:
        return pairs
    used: dict[str, int] = {account: 0 for account in accounts}

    def room(account: str) -> int:
        budget = budgets.get(account)
        if budget is None:
            return 10 ** 6
        return max(0, budget.left - used[account])

    for target in targets:
        account = max(accounts, key=lambda name: (room(name), -used[name]))
        if room(account) <= 0:
            break
        pairs.append((account, target))
        used[account] += 1
        if limits.exec_cap and len(pairs) >= limits.exec_cap:
            break
    return pairs


def _apply_task_mark(config: MassSendConfig, content: Content) -> Content:
    """Append the task marker to the text, the way ``taskMark`` does."""
    if not (config.task_mark_enabled and config.task_mark and content.text):
        return content
    mark = config.task_mark
    text = content.text if mark in content.text else f"{content.text}\n{mark}"
    return Content(text=text, media=content.media,
                   forward_from=content.forward_from, sticker=content.sticker,
                   reply_to=content.reply_to)


def _remove_sent(config: MassSendConfig, planned: list[Planned]) -> int:
    """Rewrite the custom list file without the targets the plan covered."""
    if not (config.remove_sent_custom_targets and config.custom_targets_file):
        return 0
    path = Path(config.custom_targets_file).expanduser()
    if not path.is_file():
        print(f"WARN: custom target file not found: {path}")
        return 0
    sent = {item.target.ref for item in planned}
    kept = [line for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and line.strip() not in sent]
    removed = len(path.read_text(encoding="utf-8").splitlines()) - len(kept)
    path.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")
    return max(0, removed)


def _resolve_media(config: MassSendConfig) -> None:
    """A relative media path is resolved against the remembered folder."""
    media = config.content.media
    if not media or not config.last_media_dir or Path(media).is_absolute():
        return
    candidate = Path(config.last_media_dir).expanduser() / media
    if candidate.exists():
        config.content.media = str(candidate)


#: The page's own validation sentences, kept as they are worded in the dump
#: (1741448-1453). The two bound checks are refusals; the rest are warnings the
#: page still lets you start with.
LIMIT_CEILING = 100_000


def validate(config: MassSendConfig, accounts: list[str],
             targets: list[object]) -> tuple[list[str], list[str]]:
    """``(errors, warnings)`` for the page's own configuration rules."""
    errors: list[str] = []
    warnings: list[str] = []
    limits = config.limits
    for name, value in (("success", limits.success_cap),
                        ("attempt", limits.exec_cap)):
        if value < 0 or value > LIMIT_CEILING:
            errors.append(f"Per-account {name} limit must be between 0 and "
                          f"{LIMIT_CEILING}, is {value}")
    if (limits.success_cap and limits.exec_cap
            and limits.exec_cap < limits.success_cap):
        errors.append("Per-account attempt limit must be greater than or "
                      "equal to success limit")
    total = len(targets)
    if limits.success_cap and limits.success_cap > total:
        warnings.append(f"Per-account limit ({limits.success_cap}) is higher "
                        f"than total targets ({total}), limit will not be "
                        f"effective")
    if config.load_balance and len(accounts) == 1:
        warnings.append("Load balancing is enabled but only one account is "
                        "selected")
    capacity = limits.daily_limit * max(1, len(accounts))
    if limits.daily_limit and capacity < total:
        warnings.append(f"Daily limit implies it may take multiple days: "
                        f"capacityPerDay={capacity} targets, "
                        f"totalTargets={total}")
    return errors, warnings


def run(config: MassSendConfig, ctx: RunContext) -> int:
    if not config.targets:
        ctx.note("no targets given", "error")
        print("ERROR: no targets (use --targets or --targets-file)")
        return 2

    accounts = ctx.bind(config.account_labels or None)
    if not accounts:
        ctx.note("no account bound, the plan is printed without one", "warn")

    errors, warnings = validate(config, [a.label for a in accounts],
                               config.targets)
    for message in errors:
        print(f"ERROR: {message}")
    if errors:
        return 2
    for message in warnings:
        print(f"warning: {message}")

    _resolve_media(config)
    content, source = build_content(
        config.content, AIClient(ctx.settings.ai, ctx.settings.ai.timeout)
    )

    budget_of = _budgets(accounts, config.limits, ctx)
    labels = [a.label for a in accounts]
    if config.load_balance:
        pairs = _balanced(labels, config.targets, config.limits, budget_of)
        order = "load_balance"
    else:
        pairs = _interleave(labels, config.targets, config.limits,
                            random.Random(config.limits.threads))
        order = "round_robin"
    planned = schedule(pairs, config.limits, accounts=labels, budgets=budget_of)
    skipped = len(pairs) - len(planned)
    if skipped:
        left = {label: b.left for label, b in budget_of.items()}
        print(f"BUDGET: {skipped} pair(s) skipped - daily limit reached; "
              f"left per account: {left}")
    now = datetime.now(UTC).replace(microsecond=0)
    start, start_reason = _start_time(config, now)
    contents = {account.label: _apply_task_mark(config, content)
                for account in accounts}

    print(f"task: {config.task_name}")
    print(f"accounts: {len(accounts)}  targets: {len(config.targets)}  "
          f"threads: {config.limits.threads}  content source: {source}")
    print(f"start: {start:%Y-%m-%d %H:%M:%S} ({start_reason})  order: {order}")
    if config.task_mark_enabled and config.task_mark:
        print(f"task mark: {config.task_mark}")
    dropped = _remove_sent(config, planned)
    if dropped:
        print(f"removed {dropped} sent target(s) from {config.custom_targets_file}")
    for line in render(planned, contents, start):
        print(line)
    if not planned and pairs and not skipped:
        removed_note = (f"{dropped} already sent (dedup)" if dropped
                        else "no filter matched")
        print(f"EXPLAIN: nothing scheduled - {removed_note}")

    # An exhausted budget or a target that filtered out leaves the plan empty;
    # indexing the last entry first crashed the run instead of reporting that.
    eta = (planned[-1].offset + config.limits.account_min) if planned else 0.0
    print(f"PLAN: {len(planned)} messages, {len(accounts)} accounts, "
          f"ETA {format_duration(eta)}")

    task = open_task(ctx, config.task_name, FEATURE,
                     {"targets": [t.ref for t in config.targets],
                      "limits": asdict(config.limits)}, len(planned))
    if not ctx.live:
        for item in planned:
            ctx.store.add_result(task.id, item.account, item.target.ref,
                                 "planned")
        print("dry-run: nothing was sent")
        close_task(ctx, task, ok=len(planned), failed=0, status="dry-run")
        return 0

    sender = ctx.sender()
    plan_lines = render(planned, contents, start)
    pairs = list(zip(plan_lines, planned, strict=True))
    sent = failed = 0
    # ``threads`` is not decoration: one lane per account is what makes the
    # interval a promise to the server.  A single thread keeps the old
    # straight-line loop, so the printed order is unchanged at threads=1.
    lanes = lanes_by(lambda pair: pair[1].account, pairs,
                     config.limits.threads)
    counters = {"sent": 0, "failed": 0}
    lock = threading.Lock()

    def send_lane(lane: list[tuple[str, object]]) -> None:
        backoff = PeerFloodBackoff()
        attempts: dict[tuple[str, str], int] = {}
        elapsed = 0.0
        for line, item in lane:
            pause(ctx, item.offset - elapsed)
            elapsed = item.offset
            result = sender.send(item.account, item.target,
                                 contents[item.account])
            with lock:
                if result.ok:
                    counters["sent"] += 1
                    ctx.store.add_result(task.id, item.account,
                                         item.target.ref, "sent",
                                         detail=str(result.message_id))
                    print(f"✅ SENT {line} msg_id={result.message_id}")
                    continue
                counters["failed"] += 1
                ctx.store.add_result(task.id, item.account, item.target.ref,
                                     "failed", result.code, result.detail)
                print(f"❌ FAIL {line} code={result.code} "
                      f"detail={result.detail}")
            if result.code == PEER_FLOOD:
                key = (item.account, item.target.ref)
                attempts[key] = attempts.get(key, 0) + 1
                wait = backoff.wait_for(attempts[key] - 1)
                ctx.note(f"PEER_FLOOD backoff {wait:.0f}s for {item.account}")

    if len(lanes) <= 1:
        send_lane(lanes[0] if lanes else [])
    else:
        with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
            for future in [pool.submit(send_lane, lane) for lane in lanes]:
                future.result()
    sent, failed = counters["sent"], counters["failed"]
    close_task(ctx, task, ok=sent, failed=failed)
    print(f"DONE: sent={sent} failed={failed}")
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    config = MassSendConfig.from_dict(raw, ctx.settings, dry_run=not ctx.live)
    return run(config, ctx)
