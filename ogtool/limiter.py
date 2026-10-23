"""Pacing: intervals, jitter, daily budgets and PEER_FLOOD backoff.

Every feature that sends anything goes through :class:`Pacer`, so the numbers
a task file carries (single account interval, single group interval, threads,
daily cap) are applied the same way everywhere, and the flood wait policy is
written once.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date


@dataclass(slots=True)
class Limits:
    """The numbers the reference client shows on its task pages."""

    account_min: float = 5.0
    account_max: float = 12.0
    group_interval: float = 3.0
    threads: int = 1
    daily_limit: int = 50
    success_cap: int = 0
    exec_cap: int = 0

    def __post_init__(self) -> None:
        if self.account_max < self.account_min:
            self.account_max = self.account_min
        self.threads = max(1, int(self.threads))
        self.account_min = max(0.0, float(self.account_min))
        self.account_max = max(0.0, float(self.account_max))
        self.group_interval = max(0.0, float(self.group_interval))


@dataclass(slots=True)
class PeerFloodBackoff:
    """Exponential wait after PEER_FLOOD, with a ceiling and some jitter.

    ``attempt`` is the number of floods already seen for the same pair; the
    first wait is ``base``, then ``base * factor``, and so on up to ``cap``.
    """

    base: float = 30.0
    factor: float = 2.0
    cap: float = 1800.0
    jitter: float = 0.25
    rng: random.Random = field(default_factory=random.Random)

    def wait_for(self, attempt: int) -> float:
        attempt = max(0, int(attempt))
        raw = min(self.cap, self.base * (self.factor ** attempt))
        spread = raw * self.jitter
        return max(1.0, min(self.cap, raw + self.rng.uniform(-spread, spread)))


@dataclass(slots=True)
class Budget:
    """A per account daily counter that rolls over at midnight."""

    limit: int
    used: int = 0
    day: str = field(default_factory=lambda: date.today().isoformat())

    def _roll(self) -> None:
        today = date.today().isoformat()
        if today != self.day:
            self.day = today
            self.used = 0

    @property
    def left(self) -> int:
        self._roll()
        return max(0, self.limit - self.used)

    def take(self, amount: int = 1) -> bool:
        self._roll()
        if self.limit and self.used + amount > self.limit:
            return False
        self.used += amount
        return True


class Pacer:
    """Produces the delay before each planned message.

    The clock is injectable so tests get exact numbers instead of sleeping.
    """

    def __init__(self, limits: Limits, rng: random.Random | None = None) -> None:
        self.limits = limits
        self.rng = rng or random.Random()
        self._last_account: dict[str, float] = {}
        self._last_target: dict[str, float] = {}
        self.now = 0.0

    def delay_for(self, account: str, target: str) -> float:
        """Seconds to wait before the next message from this account."""
        wait = self.rng.uniform(self.limits.account_min, self.limits.account_max)
        if self.limits.group_interval > 0:
            previous = self._last_target.get(target)
            if previous is not None:
                since = self.now - previous
                wait = max(wait, self.limits.group_interval - since)
        return max(0.0, wait)

    def mark(self, account: str, target: str) -> None:
        self._last_account[account] = self.now
        self._last_target[target] = self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def eta(self, planned: int) -> float:
        """Rough total time: mean account interval times the message count."""
        if planned <= 0:
            return 0.0
        mean = (self.limits.account_min + self.limits.account_max) / 2.0
        per_thread = planned / max(1, self.limits.threads)
        return per_thread * max(mean, self.limits.group_interval)


def format_duration(seconds: float) -> str:
    """``3750`` -> ``1h02m30s``."""
    total = int(round(max(0.0, seconds)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"
