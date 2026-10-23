"""The batch loop every queue shaped page shares.

Eight of the eleven P1 pages are the same thing underneath: a list of jobs, one
account per job, an interval, a couple of caps, a flood policy and five
counters.  Keeping the loop here means the retry rule, the PEER_FLOOD backoff
and the MISSING/FLOOD buckets are written once and behave the same on every
page -- and a dry run prints exactly what a live run would do, minus the wait.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .limiter import PeerFloodBackoff, format_duration
from .progress import Stats
from .simulate import (
    CHANNEL_PRIVATE,
    INVITE_HASH_EXPIRED,
    PEER_FLOOD,
    TIMEOUT,
    USER_ID_INVALID,
    USER_NOT_MUTUAL_CONTACT,
    USER_PRIVACY_RESTRICTED,
)
from .targets import Target

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .runtime import RunContext
    from .senders import SendResult

DEFAULT_MISSING = (
    USER_PRIVACY_RESTRICTED,
    USER_NOT_MUTUAL_CONTACT,
    USER_ID_INVALID,
    CHANNEL_PRIVATE,
    INVITE_HASH_EXPIRED,
)


@dataclass(slots=True)
class Job:
    """One line of work: which account does what to whom."""

    account: str
    subject: str
    target: Target | None = None
    member: Target | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Outcome:
    """What a batch produced, in the shapes the pages export."""

    stats: Stats = field(default_factory=Stats)
    missing: list[str] = field(default_factory=list)
    flood: list[str] = field(default_factory=list)
    timeouts: list[str] = field(default_factory=list)
    waited: float = 0.0
    stopped: str = ""

    def note_missing(self, subject: str) -> None:
        if subject not in self.missing:
            self.missing.append(subject)

    def note_flood(self, subject: str) -> None:
        if subject not in self.flood:
            self.flood.append(subject)

    def note_timeout(self, subject: str) -> None:
        if subject not in self.timeouts:
            self.timeouts.append(subject)


def execute(
    jobs: Iterable[Job],
    ctx: RunContext,
    call: Callable[[Job], SendResult],
    *,
    total: int = 0,
    success_cap: int = 0,
    exec_cap: int = 0,
    flood_cap: int = 0,
    retry_cap: int = 0,
    missing_codes: Iterable[str] = DEFAULT_MISSING,
    label: str = "job",
    quiet: bool = False,
) -> Outcome:
    """Run every job, honouring the caps, the retry rule and the flood policy.

    ``exec_cap`` counts attempts, ``success_cap`` counts successes; whichever
    trips first ends the run with ``outcome.stopped`` naming it, which is how
    the reference pages stop early without pretending they finished.
    """
    items = list(jobs)
    outcome = Outcome()
    outcome.stats.total = total or len(items)
    missing = set(missing_codes)
    backoff = PeerFloodBackoff()
    sender = ctx.sender()

    for index, job in enumerate(items, start=1):
        if exec_cap and index > exec_cap:
            outcome.stopped = "exec_cap"
            break
        if success_cap and outcome.stats.ok >= success_cap:
            outcome.stopped = "success_cap"
            break

        floods = 0
        retries = 0
        result: SendResult | None = None
        while True:
            result = call(job)
            if result.ok:
                break
            if result.code == PEER_FLOOD:
                outcome.note_flood(job.subject)
                floods += 1
                if floods > flood_cap:
                    break
                wait = result.retry_after or backoff.wait_for(floods - 1)
                outcome.waited += wait
                if not quiet:
                    print(f"  PEER_FLOOD backoff {wait:.0f}s "
                          f"(attempt {floods}/{flood_cap}) {job.subject}")
                if ctx.live:
                    time.sleep(wait)
                continue
            if result.retryable and retries < retry_cap:
                retries += 1
                wait = result.retry_after or backoff.wait_for(retries - 1)
                outcome.waited += wait
                if not quiet:
                    print(f"  retry {result.code} after {wait:.0f}s "
                          f"(attempt {retries}/{retry_cap}) {job.subject}")
                if ctx.live:
                    time.sleep(wait)
                continue
            break

        assert result is not None
        outcome.stats.record(result.ok, result.code)
        if not result.ok:
            if result.code in missing:
                outcome.note_missing(job.subject)
            if result.code == TIMEOUT:
                outcome.note_timeout(job.subject)
        if not quiet:
            state = "ok" if result.ok else result.code
            print(f"[{index:03d}/{outcome.stats.total or len(items):03d}] "
                  f"account={job.account} {label}={job.subject} -> {state}")

    del sender
    return outcome


def print_outcome(outcome: Outcome, *, processed_label: str = "Processed",
                  eta_per_job: float = 0.0) -> None:
    """The counters every page shows, then the PLAN line."""
    for line in outcome.stats.lines(processed_label):
        print(line)
    if outcome.stopped:
        print(f"stopped: {outcome.stopped}")
    if outcome.waited:
        print(f"waited: {format_duration(outcome.waited)}")
    profile = [p for p in outcome.missing if p] if outcome.missing else []
    if profile:
        print(f"MISSING: {len(profile)}")
    if outcome.flood:
        print(f"FLOOD: {len(outcome.flood)}")
    if outcome.timeouts:
        print(f"TIMEOUT: {len(outcome.timeouts)}")
    if eta_per_job:
        print(f"ETA {format_duration(outcome.stats.processed * eta_per_job)}")
