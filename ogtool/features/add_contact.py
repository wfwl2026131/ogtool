"""#16 add contacts - add contacts with caps per target and per account.

Fields: success cap, execution cap, successes per target, request interval in
milliseconds, operation interval range in milliseconds, threads, and the contact
list.  The per target cap is what the page calls ``per target success cap``: the same
number is never added twice beyond it.

The contact list has three sources, as the page does (``contactsInputMode``):
typed one per line, a text blob, or a file.  ``countExistingContacts`` asks
every account how many contacts it already has before the run starts, because
"added 40" means something different on an account that started at 800.
"""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..config import ConfigError, Settings
from ..flags import Flag
from ..limiter import PeerFloodBackoff, format_duration
from ..runtime import RunContext
from ..targets import parse_many
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    lanes_by,
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
    Flag("--contacts-text", "input_text", str,
         "the same list as one blob, one contact per line"),
    Flag("--count-existing", "count_existing", None,
         "ask every account how many contacts it has before adding",
         action="store_true"),
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
         "one lane per account, lanes add at the same time"),
    Flag("--flood-cap", "flood_cap", int, "PEER_FLOOD retries per contact"),
)


INPUT_MODES = ("manual", "text", "file")


def read_contacts(data: dict) -> tuple[list[str], str]:
    """The contact list and where it came from: the page's three input modes.

    ``manual`` reads ``contacts``, ``text`` reads the ``input_text`` blob (one
    contact per line), ``file`` reads ``files``.  With no mode given the file
    wins, then the blob, then the typed list — the order the page fills them.
    """
    from ..report import read_lines

    mode = str(data.get("input_mode") or "").lower()
    if mode and mode not in INPUT_MODES:
        raise ConfigError(
            f"{SECTION}: input_mode must be manual, text or file, is {mode}")

    files = data.get("files") or []
    if isinstance(files, str):
        files = [files]
    blob = str(data.get("input_text") or "")
    typed = lines(data, "contacts")

    if not mode:
        mode = "file" if files else ("text" if blob.strip() else "manual")
    if mode == "file":
        contacts = list(typed)
        for path in files:
            contacts.extend(read_lines(str(path)))
    elif mode == "text":
        contacts = typed + [row for row in blob.splitlines() if row.strip()]
    else:
        contacts = typed
    return contacts, mode


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
    #: ``manual`` (typed), ``text`` (one blob) or ``file`` (one file per line).
    input_mode: str = "manual"
    input_text: str = ""
    count_existing: bool = False
    task_name: str = "add-contact"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> ContactConfig:
        del settings
        data = section(raw, SECTION)
        contacts, mode = read_contacts(data)
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
            input_mode=mode,
            input_text=str(data.get("input_text") or ""),
            count_existing=bool(data.get("count_existing")),
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


def _count_existing(config: ContactConfig, labels: list[str],
                    ctx: RunContext) -> dict[str, int]:
    """Ask each account for its contact count; unknown stays unknown."""
    sender = ctx.sender()
    counts: dict[str, int] = {}
    for account in labels:
        result = sender.get_contacts(account)  # type: ignore[attr-defined]
        if result.ok:
            counts[account] = result.count
            print(f"existing contacts: {account}={result.count}")
        else:
            # A failed count is reported, not filled in with zero: the page
            # would otherwise claim an account has no contacts at all.
            print(f"existing contacts: {account} unknown ({result.code})")
    total = sum(counts.values())
    print(f"existing contacts total: {total} over {len(counts)} account(s)")
    return counts


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
        input_mode=config.input_mode,
        queue=len(jobs),
    )
    if config.count_existing:
        _count_existing(config, labels, ctx)

    task = open_task(ctx, config.task_name, FEATURE,
                     {"contacts": config.contacts}, len(jobs))
    sender = ctx.sender()

    positions = {job: index for index, job in enumerate(jobs, start=1)}
    total = len(jobs)
    backoff = PeerFloodBackoff()

    def add_lane(lane: list[tuple[str, str, int]]) -> tuple[int, int, float]:
        ok_count = failed_count = 0
        waited = 0.0
        rng = random.Random(lane[0][0])
        for position, (account, contact, round_index) in enumerate(lane):
            if position:
                delay = gap(config, rng)
                waited += delay
                pause(ctx, delay)
            target = parse_many([contact], "custom")[0]
            result = sender.add_contact(account, target)
            attempts = 0
            # ``floodCap``: "PEER_FLOOD retries per contact" - the contact is
            # worth another try after a rest, and the page counts the tries.
            while result.code == "PEER_FLOOD" and attempts < config.flood_cap:
                attempts += 1
                delay = backoff.wait_for(attempts - 1)
                print(f"  PEER_FLOOD backoff {delay:.0f}s "
                      f"(attempt {attempts}/{config.flood_cap})")
                waited += delay
                pause(ctx, delay)
                result = sender.add_contact(account, target)
            if result.ok:
                ok_count += 1
                record(ctx, task, account, contact, True)
                state = "ok"
            else:
                failed_count += 1
                record(ctx, task, account, contact, False, result.code,
                       result.detail)
                state = result.code
            print(f"[{positions[(account, contact, round_index)]:03d}/"
                  f"{total:03d}] account={account} contact={contact} "
                  f"round={round_index} -> {state}")
        return ok_count, failed_count, waited

    # One lane per account: the operation interval is a promise to that
    # account, so two threads must never work the same one.
    lanes = lanes_by(lambda job: job[0], jobs, config.threads)
    if len(lanes) <= 1:
        outcomes = [add_lane(lanes[0] if lanes else [])]
    else:
        with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
            outcomes = list(pool.map(add_lane, lanes))

    ok = sum(row[0] for row in outcomes)
    failed = sum(row[1] for row in outcomes)
    waited = sum(row[2] for row in outcomes)

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
