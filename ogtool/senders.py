"""Sending: one interface, two backends.

``DryRunSender`` prints everything and touches nothing; ``TelethonSender`` is
the only place in the package that talks to Telegram, so ``--dry-run`` can run
with no credentials, no session and no network, and the error codes stay in one
place.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .targets import Target

# The code names the reference client exports for a failed attempt.
BOT_RESULTS_EMPTY = "BOT_RESULTS_EMPTY"
CHAT_WRITE_FORBIDDEN = "CHAT_WRITE_FORBIDDEN"
SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
USER_BANNED_IN_CHANNEL = "USER_BANNED_IN_CHANNEL"
PEER_FLOOD = "PEER_FLOOD"
FLOOD_WAIT = "FLOOD_WAIT"
TARGET_INVALID = "TARGET_INVALID"
NETWORK_ERROR = "NETWORK_ERROR"
UNKNOWN = "UNKNOWN"

_RETRYABLE = {PEER_FLOOD, FLOOD_WAIT, NETWORK_ERROR}


@dataclass(slots=True)
class SendResult:
    ok: bool
    code: str = ""
    detail: str = ""
    message_id: int = 0
    retryable: bool = False


@dataclass(slots=True)
class Content:
    """What goes out: text, an optional media path and an optional forward."""

    text: str = ""
    media: str = ""
    forward_from: str = ""

    def describe(self) -> str:
        parts = []
        if self.text:
            parts.append(self.text)
        if self.media:
            parts.append(f"media={self.media}")
        if self.forward_from:
            parts.append(f"forward={self.forward_from}")
        return " ".join(parts) or "(empty)"


def code_from_exception(error: BaseException) -> tuple[str, str, bool]:
    """Map a Telethon error to one of the exported codes."""
    name = type(error).__name__
    text = str(error)
    upper = text.upper()
    if name == "FloodWaitError" or "FLOOD_WAIT" in upper:
        return FLOOD_WAIT, text, True
    if "PEER_FLOOD" in upper:
        return PEER_FLOOD, text, True
    if name in ("ChatWriteForbiddenError",) or "CHAT_WRITE_FORBIDDEN" in upper:
        return CHAT_WRITE_FORBIDDEN, text, False
    if "USER_BANNED_IN_CHANNEL" in upper:
        return USER_BANNED_IN_CHANNEL, text, False
    if "BOT_RESULTS_EMPTY" in upper or name == "BotResponseTimeoutError":
        return BOT_RESULTS_EMPTY, text, False
    if "AUTH_KEY_UNREGISTERED" in upper or "SESSION_REVOKED" in upper:
        return SESSION_NOT_FOUND, text, False
    return UNKNOWN, f"{name}: {text}", False


class DryRunSender:
    """The backend ``--dry-run`` uses: it records and prints, never connects."""

    live = False

    def __init__(self, echo: bool = True) -> None:
        self.echo = echo
        self.sent: list[tuple[str, Target, Content]] = []
        self._counter = 0

    def send(self, account: str, target: Target, content: Content) -> SendResult:
        self._counter += 1
        self.sent.append((account, target, content))
        if self.echo:
            print(
                f"DRY-RUN send account={account} target={target.display()} "
                f"content={content.describe()}"
            )
        return SendResult(ok=True, message_id=self._counter)

    def ping(self, account: str) -> SendResult:
        """Heartbeat: a dry run only says the account exists."""
        return SendResult(ok=True)

    def close(self) -> None:
        """Nothing to release; kept so both backends share one interface."""


@dataclass(slots=True)
class TelethonSender:
    """The real backend. Imports Telethon lazily, so dry runs need no install."""

    settings: Settings
    live: bool = True
    _clients: dict[str, Any] = field(default_factory=dict)

    def _client(self, account: str) -> Any:
        if account in self._clients:
            return self._clients[account]
        api_id, api_hash = self.settings.require_credentials()
        session_path = self.settings.session_path(account)
        if not session_path.exists():
            raise RuntimeError(f"{SESSION_NOT_FOUND}: {session_path}")
        try:
            from telethon import TelegramClient
        except ImportError as error:  # pragma: no cover - live only
            raise RuntimeError("telethon is not installed") from error
        proxy = self.settings.proxy.to_telethon() if self.settings.proxy else None
        client = TelegramClient(str(session_path), api_id, api_hash, proxy=proxy)
        client.connect()
        self._clients[account] = client
        return client

    def send(self, account: str, target: Target, content: Content) -> SendResult:
        try:
            client = self._client(account)
            peer = target.ref
            if content.forward_from:
                message = client.forward_messages(peer, content.forward_from)
            elif content.media:
                message = client.send_file(
                    peer, content.media, caption=content.text or None
                )
            else:
                message = client.send_message(peer, content.text)
            return SendResult(ok=True, message_id=int(getattr(message, "id", 0)))
        except Exception as error:  # noqa: BLE001 - mapped below
            code, detail, retryable = code_from_exception(error)
            return SendResult(ok=False, code=code, detail=detail,
                              retryable=retryable)

    def ping(self, account: str) -> SendResult:
        """Ask the server for our own user, which also refreshes our presence."""
        try:
            client = self._client(account)
            me = client.get_me()
            if me is None:
                return SendResult(ok=False, code=SESSION_NOT_FOUND,
                                  detail="get_me() returned nothing")
            return SendResult(ok=True, detail=str(getattr(me, "id", "")))
        except Exception as error:  # noqa: BLE001 - mapped below
            code, detail, retryable = code_from_exception(error)
            return SendResult(ok=False, code=code, detail=detail,
                              retryable=retryable)

    def close(self) -> None:
        for client in self._clients.values():
            with contextlib.suppress(Exception):  # closing must not raise
                client.disconnect()
        self._clients.clear()
