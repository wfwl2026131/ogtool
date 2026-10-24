"""#8 batch join - join groups with a pool of accounts.

Fields from the reference page: account choice, request interval, request
timeout, per account join interval, per account join cap, concurrency and the
total.  A dry run prints every join it would perform, in the order it would
perform it, with the account that would perform it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial

from ..config import Settings
from ..flags import Flag
from ..progress import Stats
from ..runner import Job, execute, print_outcome
from ..runtime import RunContext
from ..targets import Target
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    name_of,
    num,
    open_task,
    record,
    section,
    targets_of,
)

FEATURE = "batch_join"
SECTION = "batch_join"
TITLE = "#8 批量加群"

FLAGS = (
    Flag("--targets", "targets", str, "group links, comma separated"),
    Flag("--targets-file", "files", str, "file with one link per line",
         action="append"),
    Flag("--request-interval", "request_interval", float,
         "seconds between two requests of the whole pool"),
    Flag("--request-timeout", "request_timeout", float,
         "seconds before a request is called a timeout"),
    Flag("--account-interval", "account_interval", float,
         "seconds between two joins of the same account"),
    Flag("--per-account-cap", "per_account_cap", int,
         "joins one account may perform"),
    Flag("--threads", "threads", int,
         "one lane per account, lanes join at the same time"),
    Flag("--success-cap", "success_cap", int, "stop after this many joins"),
    Flag("--exec-cap", "exec_cap", int, "stop after this many attempts"),
    Flag("--flood-cap", "flood_cap", int, "PEER_FLOOD retries per target"),
)


@dataclass(slots=True)
class JoinConfig:
    account_labels: list[str] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    request_interval: float = 5.0
    request_timeout: float = 30.0
    account_interval: float = 10.0
    per_account_cap: int = 0
    threads: int = 1
    success_cap: int = 0
    exec_cap: int = 0
    flood_cap: int = 2
    task_name: str = "batch-join"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> JoinConfig:
        del settings
        data = section(raw, SECTION)
        return cls(
            account_labels=accounts_of(raw, data),
            targets=targets_of(data, kind="group"),
            request_interval=num(data, "request_interval", 5.0),
            request_timeout=num(data, "request_timeout", 30.0),
            account_interval=num(data, "account_interval", 10.0),
            per_account_cap=num(data, "per_account_cap", 0, kind=int),
            threads=num(data, "threads", 1, kind=int, minimum=1),
            success_cap=num(data, "success_cap", 0, kind=int),
            exec_cap=num(data, "exec_cap", 0, kind=int),
            flood_cap=num(data, "flood_cap", 2, kind=int),
            task_name=name_of(raw, data, "batch-join"),
        )


def assign(accounts: list[str], targets: list[Target],
           per_account_cap: int, exec_cap: int) -> list[tuple[str, Target]]:
    """Round robin, honouring the per account cap and the overall one."""
    pairs: list[tuple[str, Target]] = []
    if not accounts or not targets:
        return pairs
    used: dict[str, int] = {account: 0 for account in accounts}
    for position, target in enumerate(targets):
        candidates = [a for a in accounts if not per_account_cap
                      or used[a] < per_account_cap]
        if not candidates:
            break
        account = candidates[position % len(candidates)]
        used[account] += 1
        pairs.append((account, target))
        if exec_cap and len(pairs) >= exec_cap:
            break
    return pairs


def gap(config: JoinConfig, job: Job, previous: Job | None = None) -> float:
    """Seconds before one join.

    The pool wide request interval always applies; two joins in a row by the
    same account also have to clear that account's own interval, which is the
    longer of the two.
    """
    wait = config.request_interval
    if previous is not None and previous.account == job.account:
        wait = max(wait, config.account_interval)
    return wait


def run(config: JoinConfig, ctx: RunContext) -> int:
    if not config.targets:
        print("ERROR: no targets (use --targets or --targets-file)")
        return 2
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts]
    pairs = assign(labels, config.targets, config.per_account_cap,
                   config.exec_cap) if labels else []
    if not labels:
        pairs = [("-", target) for target in config.targets]

    header(config.task_name,
        accounts=f"{len(labels)}"
                 + (" (no account bound, plan only)" if not labels else ""),
        groups=len(config.targets),
        request_interval=f"{config.request_interval:.0f}s",
        request_timeout=f"{config.request_timeout:.0f}s",
        account_interval=f"{config.account_interval:.0f}s",
        per_account_cap=config.per_account_cap or "-",
        concurrency=config.threads,
        total=len(pairs),
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"targets": [t.ref for t in config.targets]}, len(pairs))
    sender = ctx.sender()

    def call(job: Job) -> object:
        assert job.target is not None
        return sender.join(job.account, job.target)

    jobs = [Job(account=account, subject=target.ref, target=target)
            for account, target in pairs]
    outcome = execute(jobs, ctx, call, total=len(jobs),
                      success_cap=config.success_cap, exec_cap=config.exec_cap,
                      flood_cap=config.flood_cap, retry_cap=1, label="group",
                      interval=partial(gap, config), lanes=config.threads)

    for job in jobs:
        if "ok" not in job.extra:
            continue
        record(ctx, task, job.account, job.subject, job.extra["ok"] == "1",
               job.extra["code"])
    stats: Stats = outcome.stats
    print_outcome(outcome, eta_per_job=config.account_interval)
    close_task(ctx, task, ok=stats.ok, failed=stats.failed)
    return 0 if not stats.failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(JoinConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["JoinConfig", "TITLE", "assign", "main", "run"]
