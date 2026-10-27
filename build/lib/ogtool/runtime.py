"""What a feature needs at run time, assembled once by the CLI."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .accounts import AccountError, AccountPool
from .config import Settings
from .senders import DryRunSender, TelethonSender
from .simulate import Script
from .store import Account, Store


@dataclass(slots=True)
class RunContext:
    settings: Settings
    store: Store
    log: logging.Logger
    live: bool = False
    task_id: int | None = None
    script: Script = field(default_factory=Script)
    degraded: bool = False
    #: Seconds this run is pacing itself by, accumulated by ``pause``. A dry run
    #: reports the interval instead of sleeping it, so this is the plan's own
    #: answer to "how long would this take" - and it is measured in one place
    #: instead of each page keeping its own total.
    waited: float = 0.0
    ai: object | None = field(default=None, repr=False)
    _sender: object | None = field(default=None, repr=False)

    def sender(self) -> DryRunSender | TelethonSender:
        if self._sender is None:
            self._sender = (
                TelethonSender(self.settings) if self.live
                else DryRunSender(script=self.script)
            )
        return self._sender  # type: ignore[return-value]

    @property
    def pool(self) -> AccountPool:
        return AccountPool(self.settings, self.store)

    def bind(self, labels: list[str] | None) -> list[Account]:
        """The accounts a task runs with.

        An empty pool is not a failure in a dry run: there is simply nothing to
        bind, and the plan is still worth printing (that is what makes the P0
        checks possible on a bare machine).  ``--live`` keeps it an error,
        because a real send needs a session behind it.
        """
        try:
            accounts = self.pool.resolve(labels)
        except AccountError as error:
            raise AccountError(str(error)) from error
        if accounts:
            return accounts
        if self.live:
            raise AccountError(
                "SESSION_NOT_FOUND: no account in the pool; add one with "
                "'accounts add' or drop --live"
            )
        self.degraded = True
        return []

    def close(self) -> None:
        if self._sender is not None:
            self._sender.close()  # type: ignore[attr-defined]

    def note(self, message: str, level: str = "info") -> None:
        self.log.info(message)
        self.store.add_log(self.task_id, message, level)
