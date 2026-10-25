"""#18 mass send v2 - message every account's own contacts.

The page differs from #5 in one decisive way: the targets are not a list the
user pastes, they come out of each account's own contact book.  So the queue is
built per account, the same person is never messaged twice by two accounts
unless the task says so, and a rerun skips whatever the results table already
holds as sent.
"""

from __future__ import annotations

import random
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..config import ConfigError, Settings
from ..flags import Flag
from ..limiter import Budget, format_duration
from ..runtime import RunContext
from ..senders import Content, SendResult
from ..store import Account
from ..targets import collect, parse_many
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    lanes_by,
    name_of,
    num,
    open_task,
    pause,
    record,
    section,
    text,
)

FEATURE = "mass_send_v2"
SECTION = "mass_send_v2"
TITLE = "#18 群发私信v2"

FLAGS = (
    Flag("--text", "text", str, "message text"),
    Flag("--media", "media", str, "message media path"),
    Flag("--targets", "targets", str,
         "targets to message instead of the contact books, comma separated"),
    Flag("--targets-file", "targets_files", str,
         "file with one target per line", action="append"),
    Flag("--contact", "contact", str,
         "simulated contact: account|name|ref (repeatable)", action="append"),
    Flag("--account-interval-min", "account_interval_min", float,
         "minimum seconds between two sends of one account"),
    Flag("--account-interval-max", "account_interval_max", float,
         "maximum seconds between two sends of one account"),
    Flag("--group-interval", "group_interval", float,
         "seconds between two messages into the same chat"),
    Flag("--threads", "threads", int,
         "one lane per account, lanes send at the same time"),
    Flag("--daily-limit", "daily_limit", int, "messages one account may send a day"),
    Flag("--success-cap", "success_cap", int, "total successes"),
    Flag("--exec-cap", "exec_cap", int, "total attempts"),
    Flag("--allow-duplicates", "allow_duplicates", str,
         "message one contact from every account that has it"),
    Flag("--resume", "resume", bool,
         "skip pairs already delivered by an earlier run (--no-resume "
         "starts over)", action="bool_pair"),
)


@dataclass(slots=True)
class Contact:
    """One entry of an account's contact book."""

    account: str
    ref: str
    name: str = ""

    def display(self) -> str:
        return self.name or self.ref


def parse_contacts(values: list[str] | None, data: dict) -> dict[str, list[Contact]]:
    """Contacts per account, from ``--contact account|name|ref`` or the YAML map.

    The YAML form is a mapping::

        contacts:
          demo1: ["Ann|@ann", "Bob|@bob"]
          demo2: ["@cid"]
    """
    book: dict[str, list[Contact]] = {}
    for entry in values or []:
        parts = [part.strip() for part in str(entry).split("|")]
        if len(parts) < 2 or not parts[0]:
            raise ConfigError(
                f"--contact needs account|ref or account|name|ref, got {entry!r}"
            )
        account = parts[0]
        if len(parts) >= 3:
            book.setdefault(account, []).append(Contact(account, parts[2], parts[1]))
        else:
            book.setdefault(account, []).append(Contact(account, parts[1]))
    raw = data.get("contacts") or {}
    if isinstance(raw, dict):
        for account, items in raw.items():
            if isinstance(items, str):
                items = [part.strip() for part in items.replace(",", "\n").splitlines()
                         if part.strip()]
            for item in items or []:
                pieces = [part.strip() for part in str(item).split("|")]
                if len(pieces) >= 2:
                    book.setdefault(str(account), []).append(
                        Contact(str(account), pieces[1], pieces[0]))
                elif pieces and pieces[0]:
                    book.setdefault(str(account), []).append(
                        Contact(str(account), pieces[0]))
    return book


@dataclass(slots=True)
class MassV2Config:
    account_labels: list[str] = field(default_factory=list)
    use_own_contacts: bool = True
    text: str = ""
    media: str = ""
    account_interval_min: float = 5.0
    account_interval_max: float = 12.0
    group_interval: float = 3.0
    threads: int = 1
    daily_limit: int = 50
    success_cap: int = 0
    exec_cap: int = 0
    allow_duplicates: bool = False
    resume: bool = True
    #: ``perTargetSuccessCap``: how many messages one contact may get.
    per_target_success_cap: int = 0
    #: ``requestIntervalMsec``: the gap one account keeps between requests.
    request_interval_ms: int = 1000
    #: ``countExistingContacts``: report what every account already has.
    count_existing: bool = False
    targets: list[str] = field(default_factory=list)
    simulated: dict[str, list[Contact]] = field(default_factory=dict)
    task_name: str = "mass-send-v2"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> MassV2Config:
        data = section(raw, SECTION)
        if not text(data, "text") and not text(data, "media"):
            raise ConfigError(f"{SECTION}: no message text or media given")
        return cls(
            account_labels=accounts_of(raw, data),
            use_own_contacts=bool(data.get("use_own_contacts", True)),
            text=text(data, "text"),
            media=text(data, "media"),
            account_interval_min=num(data, "account_interval_min", 5.0),
            account_interval_max=num(data, "account_interval_max", 12.0),
            group_interval=num(data, "group_interval", 3.0),
            threads=settings.runtime.clamp_threads(
                num(data, "threads", 1, kind=int, minimum=1)),
            daily_limit=num(data, "daily_limit", 50, kind=int, minimum=0),
            success_cap=num(data, "success_cap", 0, kind=int),
            exec_cap=num(data, "exec_cap", 0, kind=int),
            allow_duplicates=bool(data.get("allow_duplicates", False)),
            per_target_success_cap=num(data, "per_target_success_cap", 0,
                                       kind=int, minimum=0),
            request_interval_ms=num(data, "request_interval_ms", 1000,
                                    kind=int, minimum=0),
            count_existing=bool(data.get("count_existing")),
            resume=bool(data.get("resume", True)),
            targets=[target.ref for target in collect(
                text(data, "targets"), data.get("targets_files") or [])],
            simulated=parse_contacts(data.get("contact"), data),
            task_name=name_of(raw, data, "mass-send-v2"),
        )


def distribute_targets(refs: list[str], labels: list[str],
                       allow_duplicates: bool) -> dict[str, list[Contact]]:
    """Spread an explicit target list over the bound accounts.

    One target is messaged by one account unless duplicates are allowed: the
    whole point of the second version is that a contact is not approached
    twice, and that rule has to survive a list pasted by hand.
    """
    book: dict[str, list[Contact]] = {label: [] for label in labels}
    if not labels:
        return book
    if allow_duplicates:
        for label in labels:
            book[label] = [Contact(label, ref) for ref in refs]
        return book
    for index, ref in enumerate(refs):
        label = labels[index % len(labels)]
        book[label].append(Contact(label, ref))
    return book


@dataclass(slots=True)
class Pair:
    """One planned message: who sends it, to whom and when."""

    index: int
    account: str
    target: str
    name: str = ""
    offset: float = 0.0

    def display(self) -> str:
        return self.target


@dataclass(slots=True)
class Plan:
    pairs: list[Pair] = field(default_factory=list)
    resumed: int = 0
    duplicates: int = 0
    over_budget: int = 0
    eta: float = 0.0


def dedupe_contacts(book: dict[str, list[Contact]],
                    allow_duplicates: bool) -> tuple[list[Contact], int]:
    """Flatten the book, keeping one entry per contact unless told otherwise."""
    flat: list[Contact] = []
    seen: set[str] = set()
    dropped = 0
    for account in book:
        for contact in book[account]:
            if not allow_duplicates and contact.ref in seen:
                dropped += 1
                continue
            seen.add(contact.ref)
            flat.append(contact)
    return flat, dropped


def schedule(flat: list[Contact], config: MassV2Config, *,
             already: set[tuple[str, str]] | None = None,
             budgets: dict[str, Budget] | None = None,
             rng: random.Random | None = None) -> Plan:
    """Turn contacts into timed pairs honouring both intervals and the caps."""
    rng = rng or random.Random(0)
    plan = Plan()
    clocks: dict[str, float] = {}
    target_clock: dict[str, float] = {}
    for contact in flat:
        if config.exec_cap and len(plan.pairs) + plan.resumed >= config.exec_cap:
            break
        if config.success_cap and len(plan.pairs) >= config.success_cap:
            break
        key = (contact.account, contact.ref)
        if already and key in already:
            plan.resumed += 1
            continue
        budget = (budgets or {}).get(contact.account)
        if budget is not None and not budget.take():
            plan.over_budget += 1
            continue
        lane = clocks.get(contact.account, 0.0)
        gap = rng.uniform(config.account_interval_min, config.account_interval_max)
        at = max(lane, target_clock.get(contact.ref, 0.0))
        plan.pairs.append(Pair(len(plan.pairs) + 1, contact.account,
                               contact.ref, contact.name, at))
        clocks[contact.account] = at + gap
        target_clock[contact.ref] = at + config.group_interval
    plan.eta = (plan.pairs[-1].offset + config.account_interval_min
                if plan.pairs else 0.0)
    return plan


def render(plan: Plan, content: Content) -> list[str]:
    lines: list[str] = []
    for pair in plan.pairs:
        lines.append(
            f"[{pair.index:03d}/{len(plan.pairs):03d}] "
            f"account={pair.account} target={pair.target} "
            f"name={pair.name or '-'} delay={pair.offset:.0f}s "
            f"content={content.describe()}"
        )
    return lines


def seed_contacts(sender: object,
                  simulated: dict[str, list[Contact]]) -> None:
    """Hand the simulated book to the dry-run transport, so its echo matches.

    A live transport has no ``contacts`` attribute; it reads Telegram instead.
    """
    if not hasattr(sender, "contacts"):
        return
    book: dict[str, list[str]] = sender.contacts  # type: ignore[attr-defined]
    for account, items in simulated.items():
        book[account] = [f"{item.name or item.ref}|{item.ref}" for item in items]


def collect_book(sender: object, labels: list[str],
                 simulated: dict[str, list[Contact]]) -> dict[str, list[Contact]]:
    """Read every account's contact list from the transport.

    A dry run gets the simulated book; a live run asks Telegram, and an account
    whose list cannot be read simply contributes nothing.
    """
    book: dict[str, list[Contact]] = {}
    for label in labels:
        raw = getattr(sender, "get_contacts", None)
        if raw is None:
            book[label] = list(simulated.get(label) or [])
            continue
        result: SendResult = raw(label)
        if not result.ok:
            print(f"contacts: account={label} -> {result.code}")
            continue
        items: list[Contact] = []
        for item in result.items:
            name, _, ref = str(item).partition("|")
            items.append(Contact(label, ref or name, name))
        for item in simulated.get(label) or []:
            if item.ref not in {entry.ref for entry in items}:
                items.append(item)
        book[label] = items
    return book


def lanes_for(pairs: list[Pair], threads: int) -> list[list[Pair]]:
    """Split the queue into at most ``threads`` lanes, one account per lane."""
    return lanes_by(lambda pair: pair.account, pairs, threads)


def _send_lane(sender: object, ctx: RunContext, task: object,
               lane: list[Pair], total: int, content: Content,
               config: MassV2Config,
               counters: dict[str, int]) -> None:
    """One lane: its own pace, its own account, and its own tally."""
    # Seeded from the lane itself, so a rerun paces the same way and a test
    # can predict the waits instead of tolerating any number.
    rng = random.Random(lane[0].index)
    for position, pair in enumerate(lane):
        if position:
            pause(ctx, rng.uniform(config.account_interval_min,
                                   config.account_interval_max))
        target = parse_many([pair.target], "custom")[0]
        result = sender.send(pair.account, target, content)  # type: ignore[attr-defined]
        if result.ok:
            with _COUNT_LOCK:
                counters["ok"] += 1
            record(ctx, task, pair.account, pair.target, True)
            print(f"[{pair.index:03d}/{total:03d}] "
                  f"account={pair.account} target={pair.target} -> ok")
            continue
        with _COUNT_LOCK:
            counters["failed"] += 1
        record(ctx, task, pair.account, pair.target, False, result.code,
               result.detail)
        print(f"[{pair.index:03d}/{total:03d}] "
              f"account={pair.account} target={pair.target} -> {result.code}")
        if result.code == "PEER_FLOOD":
            ctx.note(f"PEER_FLOOD for {pair.account}: the lane keeps going")


#: ``ok``/``failed`` are counted by every lane at once.
_COUNT_LOCK = threading.Lock()


def _cap_per_target(pairs: list, cap: int) -> tuple[list, int]:
    """Keep at most *cap* messages per contact; returns (kept, dropped)."""
    seen: dict[str, int] = {}
    kept: list = []
    dropped = 0
    for pair in pairs:
        count = seen.get(pair.target, 0)
        if count >= cap:
            dropped += 1
            continue
        seen[pair.target] = count + 1
        kept.append(pair)
    return kept, dropped


def run(config: MassV2Config, ctx: RunContext) -> int:
    accounts: list[Account] = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts]
    sender = ctx.sender()

    if config.targets:
        book = distribute_targets(config.targets, labels,
                                  config.allow_duplicates)
    else:
        seed_contacts(sender, config.simulated)
        book = collect_book(sender, labels, config.simulated) if labels else {}
    flat, duplicates = dedupe_contacts(book, config.allow_duplicates)
    already = ctx.store.sent_pairs(FEATURE) if config.resume else set()
    budgets = {label: Budget(config.daily_limit,
                             ctx.store.count_sent_today(label))
               for label in labels}
    plan = schedule(flat, config, already=already, budgets=budgets)
    plan.duplicates = duplicates
    content = Content(text=config.text, media=config.media)

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        source="targets" if config.targets else "own contacts",
        own_contacts=config.use_own_contacts,
        contacts=len(flat),
        duplicates=(f"{duplicates} dropped" if duplicates
                    else ("kept" if config.allow_duplicates else "0")),
        resumed=plan.resumed,
        over_budget=plan.over_budget,
        account_interval=f"{config.account_interval_min:.0f}-"
                         f"{config.account_interval_max:.0f}s",
        request_interval=f"{config.request_interval_ms} ms",
        per_target_cap=config.per_target_success_cap or "-",
        group_interval=f"{config.group_interval:.0f}s",
        threads=config.threads,
        daily_limit=config.daily_limit or "-",
        success_cap=config.success_cap or "-",
        exec_cap=config.exec_cap or "-",
        queue=len(plan.pairs),
    )
    if config.per_target_success_cap:
        kept, cut = _cap_per_target(plan.pairs, config.per_target_success_cap)
        plan.pairs = kept
        print(f"per target cap {config.per_target_success_cap}: "
              f"{cut} message(s) dropped")
    if config.count_existing:
        for label in labels:
            result = sender.get_contacts(label)
            state = f"{result.count}" if result.ok else f"unknown ({result.code})"
            print(f"existing contacts: {label}={state}")
    if not labels:
        print("dry-run: no account bound, only the plan is produced")

    task = open_task(ctx, config.task_name, FEATURE,
                     {"contacts": flat and len(flat),
                      "targets": len(config.targets), "text": config.text},
                     len(plan.pairs))

    if not ctx.live:
        for line in render(plan, content):
            print(line)
        print(f"PLAN: {len(plan.pairs)} messages, {len(accounts)} accounts, "
              f"ETA {format_duration(plan.eta)}")
        print("dry-run: nothing was sent")
        for pair in plan.pairs:
            ctx.store.add_result(task.id, pair.account, pair.target, "planned")
        close_task(ctx, task, ok=0, failed=0)
        return 0

    lanes = lanes_for(plan.pairs, config.threads)
    counters = {"ok": 0, "failed": 0}
    if len(lanes) <= 1:
        _send_lane(sender, ctx, task, lanes[0], len(plan.pairs), content,
                   config, counters)
    else:
        # One worker per lane: the accounts never meet, so the pacing above is
        # untouched and only the wall clock shrinks.
        with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
            work = [pool.submit(_send_lane, sender, ctx, task, lane,
                                len(plan.pairs), content, config, counters)
                    for lane in lanes]
            for job in work:
                job.result()
    ok, failed = counters["ok"], counters["failed"]
    close_task(ctx, task, ok=ok, failed=failed)
    print(f"ok: {ok}  failed: {failed}  resumed: {plan.resumed}")
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(MassV2Config.from_dict(raw, ctx.settings), ctx)


__all__ = [
    "Contact",
    "MassV2Config",
    "Pair",
    "Plan",
    "TITLE",
    "collect_book",
    "dedupe_contacts",
    "distribute_targets",
    "main",
    "parse_contacts",
    "render",
    "run",
    "schedule",
    "seed_contacts",
]
