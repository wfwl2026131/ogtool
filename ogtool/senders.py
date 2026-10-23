"""Transport: one interface, two backends.

``DryRunSender`` prints everything and touches nothing; ``TelethonSender`` is
the only place in the package that talks to Telegram.  Every operation the 22
console pages need (send, join, invite, profile, contact, resolve, participants,
proxy) goes through both, so ``--dry-run`` can run with no credentials, no
session and no network, and the error codes stay in one place.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .simulate import (
    BOT_RESULTS_EMPTY,
    CHANNEL_PRIVATE,
    CHAT_ADMIN_REQUIRED,
    CHAT_WRITE_FORBIDDEN,
    CLASSIFIED,
    FLOOD_WAIT,
    INVITE_HASH_EXPIRED,
    NETWORK_ERROR,
    OK,
    PEER_FLOOD,
    PHONE_OCCUPIED,
    SESSION_NOT_FOUND,
    TARGET_INVALID,
    TIMEOUT,
    UNKNOWN,
    USER_ALREADY_PARTICIPANT,
    USER_BANNED_IN_CHANNEL,
    USER_ID_INVALID,
    USER_NOT_MUTUAL_CONTACT,
    USER_PRIVACY_RESTRICTED,
    Script,
    describe,
)
from .targets import Target

__all__ = [
    "BOT_RESULTS_EMPTY",
    "CHANNEL_PRIVATE",
    "CHAT_ADMIN_REQUIRED",
    "CHAT_WRITE_FORBIDDEN",
    "Content",
    "DryRunSender",
    "FLOOD_WAIT",
    "INVITE_HASH_EXPIRED",
    "NETWORK_ERROR",
    "OK",
    "PEER_FLOOD",
    "PHONE_OCCUPIED",
    "SESSION_NOT_FOUND",
    "SendResult",
    "TARGET_INVALID",
    "TIMEOUT",
    "TelethonSender",
    "UNKNOWN",
    "USER_ALREADY_PARTICIPANT",
    "USER_BANNED_IN_CHANNEL",
    "USER_ID_INVALID",
    "USER_NOT_MUTUAL_CONTACT",
    "USER_PRIVACY_RESTRICTED",
    "code_from_exception",
    "result_for",
]


@dataclass(slots=True)
class SendResult:
    ok: bool
    code: str = ""
    detail: str = ""
    message_id: int = 0
    retryable: bool = False
    retry_after: float = 0.0
    count: int = 0


@dataclass(slots=True)
class Content:
    """What goes out: text, an optional media path and an optional forward."""

    text: str = ""
    media: str = ""
    forward_from: str = ""
    sticker: str = ""

    def describe(self) -> str:
        parts = []
        if self.text:
            parts.append(self.text)
        if self.media:
            parts.append(f"media={self.media}")
        if self.sticker:
            parts.append(f"sticker={self.sticker}")
        if self.forward_from:
            parts.append(f"forward={self.forward_from}")
        return " ".join(parts) or "(empty)"


def result_for(code: str, detail: str = "") -> SendResult:
    """Build a :class:`SendResult` from a simulated or mapped code.

    A classified answer (``ACTIVE``, ``CAN_SEND``, ...) counts as success but
    keeps its own code, because that code is the bucket the page sorts it into.
    """
    name, text, retryable, wait = describe(code)
    if name == OK:
        return SendResult(ok=True)
    if name in CLASSIFIED:
        return SendResult(ok=True, code=name, detail=detail or text)
    return SendResult(ok=False, code=name, detail=detail or text,
                      retryable=retryable, retry_after=wait)


def code_from_exception(error: BaseException) -> tuple[str, str, bool]:
    """Map a Telethon error to one of the exported codes."""
    name = type(error).__name__
    text = str(error)
    upper = text.upper()
    if name == "FloodWaitError" or "FLOOD_WAIT" in upper:
        return FLOOD_WAIT, text, True
    if "PEER_FLOOD" in upper:
        return PEER_FLOOD, text, True
    if "USER_ALREADY_PARTICIPANT" in upper:
        return USER_ALREADY_PARTICIPANT, text, False
    if "INVITE_HASH_EXPIRED" in upper:
        return INVITE_HASH_EXPIRED, text, False
    if "CHANNEL_PRIVATE" in upper:
        return CHANNEL_PRIVATE, text, False
    if "USER_PRIVACY_RESTRICTED" in upper:
        return USER_PRIVACY_RESTRICTED, text, False
    if "USER_NOT_MUTUAL_CONTACT" in upper:
        return USER_NOT_MUTUAL_CONTACT, text, False
    if "CHAT_ADMIN_REQUIRED" in upper:
        return CHAT_ADMIN_REQUIRED, text, False
    if "USER_ID_INVALID" in upper:
        return USER_ID_INVALID, text, False
    if name in ("ChatWriteForbiddenError",) or "CHAT_WRITE_FORBIDDEN" in upper:
        return CHAT_WRITE_FORBIDDEN, text, False
    if "USER_BANNED_IN_CHANNEL" in upper:
        return USER_BANNED_IN_CHANNEL, text, False
    if "BOT_RESULTS_EMPTY" in upper or name == "BotResponseTimeoutError":
        return BOT_RESULTS_EMPTY, text, False
    if name in ("TimeoutError", "asyncio.TimeoutError") or "TIMEOUT" in upper:
        return TIMEOUT, text, True
    if "AUTH_KEY_UNREGISTERED" in upper or "SESSION_REVOKED" in upper:
        return SESSION_NOT_FOUND, text, False
    if "PHONE_NUMBER_OCCUPIED" in upper or "PHONE_OCCUPIED" in upper:
        return PHONE_OCCUPIED, text, False
    return UNKNOWN, f"{name}: {text}", False


class DryRunSender:
    """The backend ``--dry-run`` uses: it records and prints, never connects.

    ``script`` carries the answers a task file asked for; each request pops one
    and falls back to ``ok`` once the list is exhausted.
    """

    live = False

    def __init__(self, echo: bool = True, script: Script | None = None) -> None:
        self.echo = echo
        self.script = script or Script()
        self.sent: list[tuple[str, Target, Content]] = []
        self.calls: list[tuple[str, str]] = []
        self._counter = 0

    def _answer(self, operation: str, account: str, note: str = "") -> SendResult:
        """Pop the next scripted answer, echo the request, build the result."""
        code = self.script.next()
        self.calls.append((operation, account))
        if self.echo:
            suffix = f" {note}" if note else ""
            print(f"DRY-RUN {operation} account={account}{suffix} -> {code}")
        result = result_for(code)
        if result.ok:
            self._counter += 1
            result.message_id = self._counter
        return result

    # sending ---------------------------------------------------------

    def send(self, account: str, target: Target, content: Content) -> SendResult:
        self.sent.append((account, target, content))
        return self._answer("send", account,
                            f"target={target.display()} "
                            f"content={content.describe()}")

    def ping(self, account: str) -> SendResult:
        """Heartbeat: a dry run only says the account exists."""
        self.calls.append(("ping", account))
        return SendResult(ok=True)

    # membership ------------------------------------------------------

    def join(self, account: str, target: Target) -> SendResult:
        return self._answer("join", account, f"target={target.display()}")

    def leave(self, account: str, target: Target) -> SendResult:
        return self._answer("leave", account, f"target={target.display()}")

    def invite(self, account: str, group: Target, member: Target) -> SendResult:
        return self._answer("invite", account,
                            f"group={group.display()} member={member.display()}")

    def get_participants(self, account: str, target: Target,
                         limit: int = 0) -> SendResult:
        result = self._answer("participants", account,
                              f"target={target.display()} limit={limit}")
        if result.ok:
            result.count = limit or 0
        return result

    # account and profile ---------------------------------------------

    def update_profile(self, account: str, first: str,
                       last: str = "") -> SendResult:
        return self._answer("profile", account,
                            f"first={first or '-'} last={last or '-'}")

    def add_contact(self, account: str, target: Target,
                    first: str = "") -> SendResult:
        return self._answer("contact", account,
                            f"target={target.display()} first={first or '-'}")

    def resolve(self, account: str, target: Target) -> SendResult:
        result = self._answer("resolve", account, f"target={target.display()}")
        if result.ok:
            result.detail = target.title or target.ref
        return result

    def set_proxy(self, account: str, proxy: str) -> SendResult:
        return self._answer("proxy", account, f"proxy={proxy or '-'}")

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

    def _guard(self, operation: str, account: str, action: Any) -> SendResult:
        try:
            return action()
        except Exception as error:  # noqa: BLE001 - mapped below
            code, detail, retryable = code_from_exception(error)
            return SendResult(ok=False, code=code, detail=detail,
                              retryable=retryable)

    def send(self, account: str, target: Target, content: Content) -> SendResult:
        def action() -> SendResult:
            client = self._client(account)
            peer = target.ref
            if content.forward_from:
                message = client.forward_messages(peer, content.forward_from)
            elif content.sticker and Path(content.sticker).exists():
                message = client.send_file(peer, content.sticker,
                                           caption=content.text or None)
            elif content.media:
                message = client.send_file(
                    peer, content.media, caption=content.text or None
                )
            else:
                message = client.send_message(peer, content.text)
            return SendResult(ok=True, message_id=int(getattr(message, "id", 0)))

        return self._guard("send", account, action)

    def ping(self, account: str) -> SendResult:
        """Ask the server for our own user, which also refreshes our presence."""

        def action() -> SendResult:
            client = self._client(account)
            me = client.get_me()
            if me is None:
                return SendResult(ok=False, code=SESSION_NOT_FOUND,
                                  detail="get_me() returned nothing")
            return SendResult(ok=True, detail=str(getattr(me, "id", "")))

        return self._guard("ping", account, action)

    def join(self, account: str, target: Target) -> SendResult:
        def action() -> SendResult:
            client = self._client(account)
            client.join(target.ref)
            return SendResult(ok=True)

        return self._guard("join", account, action)

    def leave(self, account: str, target: Target) -> SendResult:
        def action() -> SendResult:
            client = self._client(account)
            client.leave(target.ref)
            return SendResult(ok=True)

        return self._guard("leave", account, action)

    def invite(self, account: str, group: Target, member: Target) -> SendResult:
        def action() -> SendResult:
            client = self._client(account)
            client.invite(group.ref, member.ref)
            return SendResult(ok=True)

        return self._guard("invite", account, action)

    def get_participants(self, account: str, target: Target,
                         limit: int = 0) -> SendResult:
        def action() -> SendResult:
            client = self._client(account)
            users = client.get_participants(target.ref, limit=limit or None)
            return SendResult(ok=True, count=len(users))

        return self._guard("participants", account, action)

    def update_profile(self, account: str, first: str,
                       last: str = "") -> SendResult:
        def action() -> SendResult:
            client = self._client(account)
            client.edit_profile(first_name=first, last_name=last)
            return SendResult(ok=True)

        return self._guard("profile", account, action)

    def add_contact(self, account: str, target: Target,
                    first: str = "") -> SendResult:
        def action() -> SendResult:
            client = self._client(account)
            client.add_contact(target.ref, first or target.ref,
                               add_phone_privacy_exception=False)
            return SendResult(ok=True)

        return self._guard("contact", account, action)

    def resolve(self, account: str, target: Target) -> SendResult:
        def action() -> SendResult:
            client = self._client(account)
            entity = client.get_entity(target.ref)
            title = getattr(entity, "title", "") or getattr(entity, "username", "")
            count = int(getattr(entity, "participants_count", 0) or 0)
            return SendResult(ok=True, detail=str(title or target.ref),
                              count=count)

        return self._guard("resolve", account, action)

    def set_proxy(self, account: str, proxy: str) -> SendResult:
        def action() -> SendResult:
            client = self._client(account)
            client.set_proxy(proxy or None)
            return SendResult(ok=True)

        return self._guard("proxy", account, action)

    def close(self) -> None:
        for client in self._clients.values():
            with contextlib.suppress(Exception):  # closing must not raise
                client.disconnect()
        self._clients.clear()
