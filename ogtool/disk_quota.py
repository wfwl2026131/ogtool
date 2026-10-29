"""The disk-backed daily quotas - the reference's QuotaDiskWriter machine.

The reference keeps its per-account daily counters on disk:
``Deep::QuotaDiskWriter`` (:2265509) with ``flushToDisk`` (:2241358) and
``isLimitReachedForNamespace`` (:2241357), instantiated twice - as
``Deep::ChatDailyQuota`` (:2184666) and ``Deep::MassSendDailyQuota``
(:2184694). The RTTI surface names the whole machine: ``recordSuccessfulText``,
``accountId``, ``countFor``, ``isLimitReached``, ``limit``,
``recordSuccessfulTextForNamespace``, ``namespaceKey``, ``countForNamespace``,
``isLimitReachedForNamespace`` and ``flushToDisk(force)``.

Two user-visible verdicts ride on it: ``All %1 accounts reached per-account
daily limits (success=%2, attempt=%3), wait until tomorrow`` (:1737966) and
``All selected accounts reached today's limits; will retry at %1. (Success
limit: %2, Attempt limit: %3; 0 means unlimited.)`` (:1737953) - which is why
the counters track successes and attempts as two numbers.

ogtool's gap was real: ``mass_send`` / ``mass_send_v2`` seeded their budgets
from the results table and so already survived a restart, but the SlotMap
remapper and ``copy_chat`` kept pure in-memory dicts - a restart handed every
spent account a fresh budget. The store's ``daily_quota`` table is the disk
side of this module; the pages record into it through :func:`record_send` and
seed from it through :class:`DiskBudget`, and ``ogtool quota`` reads and
repairs the table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: The namespaces that already record here. A new sending page joins under
#: its own feature name - the table stays readable per page, which is what
#: the reference's two classes (Chat / MassSend) are for.
PUPPET_CHAT = "puppet_chat"
COPY_CHAT = "copy_chat"
NAMESPACES = (PUPPET_CHAT, COPY_CHAT)


def all_accounts_capped(accounts: int, success: int, attempt: int) -> str:
    """:1737966 verbatim - the run-level verdict when nothing can send."""
    return (
        f"All {accounts} accounts reached per-account daily limits "
        f"(success={success}, attempt={attempt}), wait until tomorrow"
    )


def selected_accounts_capped(retry: str, success_limit: int, attempt_limit: int) -> str:
    """:1737953 verbatim - the mass-send verdict with its two caps named."""
    return (
        "All selected accounts reached today's limits; will retry at "
        f"{retry}. (Success limit: {success_limit}, Attempt limit: "
        f"{attempt_limit}; 0 means unlimited.)"
    )


@dataclass(slots=True)
class DiskBudget:
    """A per-account daily counter read from - and written to - the store.

    Same shape as :class:`ogtool.limiter.Budget`, but ``take`` is one
    ``bump_daily_quota`` write and the counts come back from disk, so a
    restart reads the table instead of starting at zero. ``limit`` 0 means
    unlimited, like every other cap in the project.
    """

    store: object
    namespace: str
    account: str
    limit: int = 0
    day: str = ""

    def __post_init__(self) -> None:
        self.day = self.day or date.today().isoformat()

    def _roll(self) -> None:
        today = date.today().isoformat()
        if today != self.day:
            self.day = today

    def used(self) -> tuple[int, int]:
        """The (success, attempt) pair spent today."""
        self._roll()
        return self.store.daily_quota_counts(self.namespace, self.account, self.day)

    @property
    def left(self) -> int:
        if not self.limit:
            return 0
        success, _attempt = self.used()
        return max(0, self.limit - success)

    def take(self, amount: int = 1) -> bool:
        """Reserve one send; False when the account is spent out."""
        self._roll()
        success, _attempt = self.used()
        if amount and self.limit and success + amount > self.limit:
            return False
        self.store.bump_daily_quota(
            self.namespace, self.account, self.day, success=True
        )
        return True


def record_send(store: object, namespace: str, account: str, *, ok: bool) -> None:
    """One send written back under its namespace (the reference's
    ``recordSuccessfulTextForNamespace`` slot).

    Pages that keep their own in-memory counters for the run call this once
    per result, so the next process sees what this one spent. A dry run must
    never spend quota it never used - callers gate on ``ctx.live``.
    """
    store.bump_daily_quota(namespace, account, date.today().isoformat(), success=ok)


def seed_budget(
    store: object, namespace: str, labels: list[str]
) -> dict[str, tuple[int, int]]:
    """Today's counters for the labels a run binds - the restart read-back."""
    rows = store.daily_quota_all(namespace, date.today().isoformat())
    return {label: rows.get(label, (0, 0)) for label in labels}


def within_limit(
    store: object,
    namespace: str,
    account: str,
    success_limit: int = 0,
    attempt_limit: int = 0,
    day: str = "",
) -> bool:
    """``isLimitReachedForNamespace``: the two-limit check :1737953 prints.

    ``0`` means unlimited for either cap, and the day key defaults to today,
    which is what makes the budget roll over at midnight without a timer.
    """
    if not success_limit and not attempt_limit:
        return True
    success, attempt = store.daily_quota_counts(
        namespace, account, day or date.today().isoformat()
    )
    if success_limit and success >= success_limit:
        return False
    return not (attempt_limit and attempt >= attempt_limit)


__all__ = [
    "COPY_CHAT",
    "NAMESPACES",
    "PUPPET_CHAT",
    "DiskBudget",
    "all_accounts_capped",
    "record_send",
    "seed_budget",
    "selected_accounts_capped",
    "within_limit",
]
