"""What a feature needs at run time, assembled once by the CLI."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .accounts import AccountPool
from .config import Settings
from .senders import DryRunSender, TelethonSender
from .store import Store


@dataclass(slots=True)
class RunContext:
    settings: Settings
    store: Store
    log: logging.Logger
    live: bool = False
    task_id: int | None = None
    _sender: object | None = field(default=None, repr=False)

    def sender(self) -> DryRunSender | TelethonSender:
        if self._sender is None:
            self._sender = (
                TelethonSender(self.settings) if self.live else DryRunSender()
            )
        return self._sender  # type: ignore[return-value]

    @property
    def pool(self) -> AccountPool:
        return AccountPool(self.settings, self.store)

    def close(self) -> None:
        if self._sender is not None:
            self._sender.close()  # type: ignore[attr-defined]

    def note(self, message: str, level: str = "info") -> None:
        self.log.info(message)
        self.store.add_log(self.task_id, message, level)
