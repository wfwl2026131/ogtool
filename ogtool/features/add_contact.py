"""#16 add contacts - add contacts with caps per target and per account.

Fields: success cap, execution cap, successes per target, request interval in
milliseconds, operation interval range in milliseconds, threads, and the contact
list.  The per target cap is what the page calls ``per target success cap``: the same
number is never added twice beyond it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..config import ConfigError, Settings
from ..flags import Flag
from ..limiter import format_duration
from ..runtime import RunContext
from ..targets import parse_many
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    lines,
    name_of,
    num,
    open_task,
    pause,
    record,
    section,
)

FEATURE = "add_contact"
SECTION = "add_contact"
TITLE = "#16 批量联系人"

FLAGS = (
    Flag("--contacts", "contacts", str, "contacts, comma separated"),
    Flag("--contacts-file", "files", str, "file with one contact per line",
         action="append"),
    Flag("--success-cap", "success_cap", int, "total successes"),
    Flag("--exec-cap", "exec_cap", int, "total attempts"),
    Flag("--per-target-cap", "per_target_cap", int,
         "successes one target may reach"),
    Flag("--request-interval-ms", "request_interval_ms", int,
         "milliseconds between two requests"),
    Flag("--operation-interval-min-ms", "operation_interval_min_ms", int,
         "minimum operation interval in milliseconds"),
    Flag("--operation-interval-max-ms", "operation_interval_max_ms", int,
         "maximum operation interval in milliseconds"),
    Flag("--threads", "threads", int,
         "planning concurrency only (requests are serial)"),
    Flag("--flood-cap", "flood_cap", int, "PEER_FLOOD retries per contact"),
)


@dataclass(slots=True)
class ContactConfig:
    account_labels: list[str] = field(default_factory=list)
    contacts: list[str] = field(default_factory=list)
    success_cap: int = 0
    exec_cap: int = 0
    per_target_cap: int = 1
    request_interval_ms: int = 1000
    operation_interval_min_ms: int = 500
    operation_interval_max_ms: int = 1500
    threads: int = 1
    flood_cap: int = 2
    task_name: str = "add-contact"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> ContactConfig:
        del settings
        data = section(raw, SECTION)
        contacts = lines(data, "contacts")
        files = data.get("files") or []
        if isinstance(files, str):
            files = [files]
        from ..report import read_lines

        for path in files:
            contacts.extend(read_lines(str(path)))
        if not contacts:
            raise ConfigError(f"{SECTION}: no contacts given (--contacts)")
        return cls(
            account_labels=accounts_of(raw, data),
            contacts=_unique(contacts),
            success_cap=num(data, "success_cap", 0, kind=int),
            exec_cap=num(data, "exec_cap", 0, kind=int),
            per_target_cap=num(data, "per_target_cap", 1, kind=int),
            request_interval_ms=num(data, "request_interval_ms", 1000, kind=int),
            operation_interval_min_ms=num(
                data, "operation_interval_min_ms", 500, kind=int),
            operation_interval_max_ms=num(
                data, "operation_interval_max_ms", 1500, kind=int),
            threads=num(data, "threads", 1, kind=int, minimum=1),
            flood_cap=num(data, "flood_cap", 2, kind=int),
            task_name=name_of(raw, data, "add-contact"),
        )


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = item.strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def plan_jobs(contacts: list[str], accounts: list[str], *,
              per_target_cap: int = 1, exec_cap: int = 0,
              success_cap: int = 0) -> list[tuple[str, str, int]]:
    """``(account, contact, round)`` triples, capped per target and overall."""
    jobs: list[tuple[str, str, int]] = []
    if not accounts:
        return jobs
    for contact in contacts:
        for round_index in range(max(1, per_target_cap)):
            if exec_cap and len(jobs) >= exec_cap:
                return jobs
            if success_cap and len(jobs) >= success_cap:
                return jobs
            jobs.append((accounts[len(jobs) % len(accounts)], contact,
                         round_index + 1))
    return jobs


def gap(config: ContactConfig, rng: random.Random | None = None) -> float:
    """Seconds to wait before the next request.

    The page has two intervals: one between two requests of the same account
    and one for the operation itself.  This run is serial, so both gaps land
    between the same two requests and add up -- the conservative reading, and
    the one that keeps a live account out of PEER_FLOOD.
    """
    generator = rng or random.Random()
    return (config.request_interval_ms / 1000.0
            + generator.uniform(config.operation_interval_min_ms,
                                config.operation_interval_max_ms) / 1000.0)


def run(config: ContactConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts] or ["-"]
    jobs = plan_jobs(config.contacts, labels,
                     per_target_cap=config.per_target_cap,
                     exec_cap=config.exec_cap,
                     success_cap=config.success_cap)

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        contacts=len(config.contacts),
        success_cap=config.success_cap or "-",
        exec_cap=config.exec_cap or "-",
        per_target_cap=config.per_target_cap,
        request_interval=f"{config.request_interval_ms} ms",
        operation_interval=f"{config.operation_interval_min_ms} - "
                          f"{config.operation_interval_max_ms} ms",
        threads=config.threads,
        queue=len(jobs),
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"contacts": config.contacts}, len(jobs))
    sender = ctx.sender()

    ok = failed = 0
    waited = 0.0
    rng = random.Random()
    for index, (account, contact, round_index) in enumerate(jobs, start=1):
        if index > 1:
            delay = gap(config, rng)
            waited += delay
            pause(ctx, delay)
        target = parse_many([contact], "custom")[0]
        result = sender.add_contact(account, target)
        if result.ok:
            ok += 1
            record(ctx, task, account, contact, True)
            print(f"[{index:03d}/{len(jobs):03d}] account={account} "
                  f"contact={contact} round={round_index} -> ok")
        else:
            failed += 1
            record(ctx, task, account, contact, False, result.code, result.detail)
            print(f"[{index:03d}/{len(jobs):03d}] account={account} "
                  f"contact={contact} round={round_index} -> {result.code}")

    print(f"联系人：{len(config.contacts)}  成功: {ok}  失败: {failed}")
    if ctx.live and waited:
        print(f"waited: {format_duration(waited)}")
    print(f"PLAN: {len(jobs)} contacts, {len(accounts)} accounts, "
          f"ok {ok}")
    close_task(ctx, task, ok=ok, failed=failed)
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(ContactConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["ContactConfig", "TITLE", "gap", "main", "plan_jobs", "run"]
