"""#5 mass send.

Targets (private / group / client / custom), a content source (text / media /
forward), the interval numbers, threads and the three caps the reference page
shows.  A dry run walks the whole pipeline and prints every message it would
send; only the transport is replaced.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from ..ai import AIClient
from ..config import Settings
from ..content import ContentSpec
from ..content import build as build_content
from ..limiter import Budget, Limits, PeerFloodBackoff, format_duration
from ..runtime import RunContext
from ..senders import PEER_FLOOD, Content
from ..store import Account
from ..targets import Target, collect
from . import register

FEATURE = "mass_send"


@dataclass(slots=True)
class MassSendConfig:
    """Everything the right pane of #5 carries, with the page's own names."""

    account_labels: list[str] = field(default_factory=list)
    target_kind: str = "custom"
    targets: list[Target] = field(default_factory=list)
    content: ContentSpec = field(default_factory=ContentSpec)
    limits: Limits = field(default_factory=Limits)
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
        used = ctx.store.count_sent_today(account.label)
        budgets[account.label] = Budget(limits.daily_limit, used)
    return budgets


def run(config: MassSendConfig, ctx: RunContext) -> int:
    if not config.targets:
        ctx.note("no targets given", "error")
        print("ERROR: no targets (use --targets or --targets-file)")
        return 2

    accounts = ctx.bind(config.account_labels or None)
    if not accounts:
        ctx.note("no account bound, the plan is printed without one", "warn")

    content, source = build_content(
        config.content, AIClient(ctx.settings.ai, ctx.settings.ai.timeout)
    )

    budgets = _budgets(accounts, config.limits, ctx)
    pairs = _interleave([a.label for a in accounts], config.targets,
                        config.limits, random.Random(config.limits.threads))
    planned = schedule(pairs, config.limits, accounts=[a.label for a in accounts],
                       budgets=budgets)
    start = datetime.now(UTC).replace(microsecond=0)
    contents = {account.label: content for account in accounts}

    print(f"task: {config.task_name}")
    print(f"accounts: {len(accounts)}  targets: {len(config.targets)}  "
          f"threads: {config.limits.threads}  content source: {source}")
    for line in render(planned, contents, start):
        print(line)

    eta = planned[-1].offset + (
        config.limits.account_min if planned else 0.0
    )
    print(f"PLAN: {len(planned)} messages, {len(accounts)} accounts, "
          f"ETA {format_duration(eta)}")

    task = ctx.store.create_task(
        config.task_name, FEATURE,
        config={"targets": [t.ref for t in config.targets],
                "limits": asdict(config.limits)},
        mode="dry-run" if not ctx.live else "live",
        planned=len(planned),
    )
    ctx.task_id = task.id
    for item in planned:
        ctx.store.add_result(task.id, item.account, item.target.ref, "planned")

    if not ctx.live:
        print("dry-run: nothing was sent")
        ctx.store.finish_task(task.id, sent=0, status="dry-run")
        return 0

    sender = ctx.sender()
    backoff = PeerFloodBackoff()
    sent = failed = 0
    attempts: dict[tuple[str, str], int] = {}
    for line, item in zip(render(planned, contents, start), planned, strict=True):
        result = sender.send(item.account, item.target, contents[item.account])
        if result.ok:
            sent += 1
            ctx.store.add_result(task.id, item.account, item.target.ref, "sent",
                                 detail=str(result.message_id))
            print(f"sent {line}")
        else:
            failed += 1
            ctx.store.add_result(task.id, item.account, item.target.ref, "failed",
                                 result.code, result.detail)
            print(f"failed {result.code} {line} {result.detail}")
            if result.code == PEER_FLOOD:
                key = (item.account, item.target.ref)
                attempts[key] = attempts.get(key, 0) + 1
                wait = backoff.wait_for(attempts[key] - 1)
                ctx.note(f"PEER_FLOOD backoff {wait:.0f}s for {item.account}")
    ctx.store.finish_task(task.id, sent=sent, failed=failed)
    print(f"DONE: sent={sent} failed={failed}")
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    config = MassSendConfig.from_dict(raw, ctx.settings, dry_run=not ctx.live)
    return run(config, ctx)
