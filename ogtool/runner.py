"""The batch loop every queue shaped page shares.

Eight of the eleven P1 pages are the same thing underneath: a list of jobs, one
account per job, an interval, a couple of caps, a flood policy and five
counters.  Keeping the loop here means the retry rule, the PEER_FLOOD backoff
and the MISSING/FLOOD buckets are written once and behave the same on every
page -- and a dry run prints exactly what a live run would do, minus the wait.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Sequence
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


@dataclass
class _Budget:
    """The caps, shared by every lane, claimed one slot at a time.

    ``success_cap`` and ``exec_cap`` are promises about the whole run, so two
    lanes must not each spend the full budget: they reserve a slot under a
    lock before doing the work.
    """

    success_cap: int = 0
    exec_cap: int = 0
    attempts: int = 0
    successes: int = 0
    stopped: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def claim_attempt(self) -> bool:
        with self._lock:
            if self.exec_cap and self.attempts >= self.exec_cap:
                self.stopped = self.stopped or "exec_cap"
                return False
            self.attempts += 1
            return True

    def claim_success(self) -> bool:
        """Reserve room for one more success, or report the cap is spent."""
        with self._lock:
            if self.success_cap and self.successes >= self.success_cap:
                self.stopped = self.stopped or "success_cap"
                return False
            self.successes += 1
            return True


def _split_lanes(jobs: Sequence[Job], lanes: int,
                 lane_key: Callable[[Job], str] | None) -> list[list[Job]]:
    """Bucket jobs so one key (normally one account) never spans two lanes."""
    key = lane_key or (lambda job: job.account)
    order: list[str] = []
    bucket: dict[str, list[tuple[int, Job]]] = {}
    for position, job in enumerate(jobs):
        name = key(job)
        if name not in bucket:
            bucket[name] = []
            order.append(name)
        bucket[name].append((position, job))
    count = max(1, min(max(1, lanes), len(order)))
    lanes_out: list[list[tuple[int, Job]]] = [[] for _ in range(count)]
    for position, name in enumerate(order):
        lanes_out[position % count].extend(bucket[name])
    # Within a lane the jobs keep their planned order: the pool interval is
    # measured against the previous job, so regrouping would change the pace.
    return [list(job for _, job in sorted(lane))
            for lane in lanes_out if lane]


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
    interval: float | Callable[[Job, Job | None], float] = 0.0,
    lanes: int = 1,
    lane_key: Callable[[Job], str] | None = None,
) -> Outcome:
    """Run every job, honouring the caps, the retry rule and the flood policy.

    ``exec_cap`` counts attempts, ``success_cap`` counts successes; whichever
    trips first ends the run with ``outcome.stopped`` naming it, which is how
    the reference pages stop early without pretending they finished.

    ``interval`` is the page's pace between two requests: a number, or a
    function of ``(job, previous job)`` for the pages that show a global and a
    per account interval separately.  It is slept only in a live run -- a dry
    run reports the pace in its ETA instead of waiting for it.
    """
    from .features._common import pause

    items = list(jobs)
    outcome = Outcome()
    outcome.stats.total = total or len(items)
    missing = set(missing_codes)
    ctx.sender()
    budget = _Budget(success_cap=success_cap, exec_cap=exec_cap)
    print_lock = threading.Lock()
    outcome_lock = threading.Lock()

    def speak(text: str) -> None:
        if quiet:
            return
        with print_lock:
            print(text)

    def run_lane(lane: Sequence[Job]) -> None:
        backoff = PeerFloodBackoff()
        previous: Job | None = None
        for job in lane:
            _run_one(job, previous, backoff)
            previous = job

    def _run_one(job: Job, previous: Job | None,
                 backoff: PeerFloodBackoff) -> None:
        index = items.index(job) + 1
        if not budget.claim_attempt():
            return
        if success_cap and outcome.stats.ok >= success_cap:
            with outcome_lock:
                outcome.stopped = outcome.stopped or "success_cap"
            return

        if previous is not None:
            wait = float(interval(job, previous) if callable(interval)
                         else interval)
            pause(ctx, wait)

        floods = 0
        retries = 0
        result: SendResult | None = None
        while True:
            result = call(job)
            if result.ok:
                break
            if result.code == PEER_FLOOD:
                with outcome_lock:
                    outcome.note_flood(job.subject)
                floods += 1
                if floods > flood_cap:
                    break
                wait = result.retry_after or backoff.wait_for(floods - 1)
                with outcome_lock:
                    outcome.waited += wait
                speak(f"  PEER_FLOOD backoff {wait:.0f}s "
                      f"(attempt {floods}/{flood_cap}) {job.subject}")
                if ctx.live:
                    time.sleep(wait)
                continue
            if result.retryable and retries < retry_cap:
                retries += 1
                wait = result.retry_after or backoff.wait_for(retries - 1)
                with outcome_lock:
                    outcome.waited += wait
                speak(f"  retry {result.code} after {wait:.0f}s "
                      f"(attempt {retries}/{retry_cap}) {job.subject}")
                if ctx.live:
                    time.sleep(wait)
                continue
            break

        assert result is not None
        # The page records one row per job afterwards, so the answer has to
        # survive the loop; a job the caps stopped early never gets a row.
        job.extra["ok"] = "1" if result.ok else ""
        job.extra["code"] = result.code
        with outcome_lock:
            outcome.stats.record(result.ok, result.code)
            if not result.ok:
                if result.code in missing:
                    outcome.note_missing(job.subject)
                if result.code == TIMEOUT:
                    outcome.note_timeout(job.subject)
        state = "ok" if result.ok else result.code
        speak(f"[{index:03d}/{outcome.stats.total or len(items):03d}] "
              f"account={job.account} {label}={job.subject} -> {state}")

    lane_jobs = _split_lanes(items, lanes if ctx.live else 1, lane_key)
    if len(lane_jobs) <= 1:
        run_lane(lane_jobs[0] if lane_jobs else [])
    else:
        threads = [threading.Thread(target=run_lane, args=(lane,), daemon=True)
                   for lane in lane_jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    outcome.stopped = outcome.stopped or budget.stopped
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
