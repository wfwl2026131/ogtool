"""#21 keep online: a periodic heartbeat keeps the sessions alive."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..limiter import format_duration
from ..runtime import RunContext
from . import register
from ._common import close_task, open_task, record

FEATURE = "keep_online"


@dataclass(slots=True)
class KeepOnlineConfig:
    """The right pane of #21: account choice plus on / off."""

    #: ``usageAccountIds`` in the page's config: which accounts hold the line.
    account_labels: list[str] = field(default_factory=list)
    #: ``pingDelayMs`` in the page's config, kept here in seconds.
    interval: float = 300.0
    loops: int = 0                  # 0 = until interrupted
    jitter: float = 0.1
    task_name: str = "keep-online"

    @classmethod
    def from_dict(cls, raw: dict) -> KeepOnlineConfig:
        online = raw.get("keep_online") or {}
        # The page stores ``pingDelayMs``; a task file may use either name, and
        # ``usage_accounts`` is the same list as ``accounts``.
        ping_ms = online.get("ping_delay_ms")
        interval = (float(ping_ms) / 1000.0 if ping_ms is not None
                    else float(online.get("interval", 300)))
        labels = online.get("usage_accounts") or raw.get("accounts") or []
        return cls(
            account_labels=[str(item) for item in labels],
            interval=interval,
            loops=int(online.get("loops", 0)),
            jitter=float(online.get("jitter", 0.1)),
            task_name=str(raw.get("task_name") or "keep-online"),
        )


def beats(config: KeepOnlineConfig, accounts: list[str]) -> list[tuple[int, float, str]]:
    """The heartbeat schedule: (round, seconds from start, account).

    Accounts of one round are spread inside the interval so they never all
    ping at the same second, which is what ``jitter`` is for.
    """
    schedule: list[tuple[int, float, str]] = []
    for round_index in range(config.loops or 1):
        base = round_index * config.interval
        for position, account in enumerate(accounts):
            spread = config.interval * config.jitter * position
            schedule.append((round_index + 1, base + spread, account))
    return schedule


def run(config: KeepOnlineConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels or None)
    if not accounts:
        ctx.note("no account bound, the plan is printed without one", "warn")

    names = [account.label for account in accounts]
    schedule = beats(config, names)

    print(f"task: {config.task_name}")
    print(f"accounts: {len(accounts)} ({', '.join(names)})")
    print(f"interval: {config.interval:.0f}s  loops: "
          f"{config.loops or 'until interrupted'}")
    for round_index, offset, account in schedule:
        print(f"[{round_index:03d}] +{offset:6.1f}s heartbeat account={account}")
    print(f"PLAN: {len(schedule)} heartbeats, {len(accounts)} accounts, "
          f"ETA {format_duration(schedule[-1][1] if schedule else 0.0)}")

    task = open_task(ctx, config.task_name, FEATURE,
                     {"accounts": names, "interval": config.interval,
                      "loops": config.loops},
                     planned=len(schedule))

    if not ctx.live:
        # The plan says N heartbeats; the receipt has to show them, or "planned 1,
        # ok 0, no rows" is a claim with nothing behind it.
        for round_index, _offset, account in schedule:
            record(ctx, task, account, f"heartbeat {round_index}", ok=True)
        print("dry-run: no session was opened")
        close_task(ctx, task, ok=len(schedule), failed=0, status="dry-run")
        return 0

    sender = ctx.sender()
    started = time.monotonic()
    online = failed = 0
    try:
        for _round, offset, account in schedule:
            wait = offset - (time.monotonic() - started)
            if wait > 0:
                time.sleep(wait)
            result = sender.ping(account)
            # One row per heartbeat: the receipt's counts have to be backed by
            # rows, and the row is also the only place that names the account.
            record(ctx, task, account, f"heartbeat {_round}",
                   bool(result.ok), getattr(result, "code", ""),
                   getattr(result, "detail", ""))
            if result.ok:
                online += 1
                print(f"online {account}")
            else:
                failed += 1
                print(f"failed {result.code} {account} {result.detail}")
                ctx.note(f"heartbeat failed for {account}: {result.code}", "warn")
    except KeyboardInterrupt:  # pragma: no cover - interactive
        ctx.note("interrupted by the operator", "warn")
    finally:
        close_task(ctx, task, ok=online, failed=failed)
    print(f"DONE: online={online} failed={failed}")
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(KeepOnlineConfig.from_dict(raw), ctx)
