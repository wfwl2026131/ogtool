"""Transport: one interface, two backends.

``DryRunSender`` prints everything and touches nothing; ``TelethonSender`` is
the only place in the package that talks to Telegram.  Every operation the 22
console pages need (send, join, invite, profile, contact, resolve, participants,
proxy) goes through both, so ``--dry-run`` can run with no credentials, no
session and no network, and the error codes stay in one place.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import random
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import Settings
from .simulate import (
    BOT_RESULTS_EMPTY,
    CHANNEL_PRIVATE,
    CHAT_ADMIN_REQUIRED,
    CHAT_WRITE_FORBIDDEN,
    CLASSIFIED,
    FLOOD_WAIT,
    INVITE_HASH_EXPIRED,
    JOIN_REQUEST,
    NETWORK_ERROR,
    OK,
    PEER_FLOOD,
    PHONE_OCCUPIED,
    RETRYABLE,
    SESSION_BUSY,
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

#: Every code a run can report; a class name that lands here needs no mapping.
KNOWN_CODES = RETRYABLE | {
    CHANNEL_PRIVATE,
    CHAT_ADMIN_REQUIRED,
    CHAT_WRITE_FORBIDDEN,
    INVITE_HASH_EXPIRED,
    JOIN_REQUEST,
    OK,
    PHONE_OCCUPIED,
    SESSION_BUSY,
    SESSION_NOT_FOUND,
    TARGET_INVALID,
    UNKNOWN,
    USER_ALREADY_PARTICIPANT,
    USER_BANNED_IN_CHANNEL,
    USER_ID_INVALID,
    USER_NOT_MUTUAL_CONTACT,
    USER_PRIVACY_RESTRICTED,
}

#: A backend returned a coroutine instead of a value: the call never ran.
CLIENT_NOT_AWAITED = "CLIENT_NOT_AWAITED"

#: Pages that put a message into somebody's chat. A daily sending budget must be
#: counted against these only: ``results.status`` says "sent" for every page that
#: succeeded, and exports or heartbeats are not messages.
SENDING_FEATURES: tuple[str, ...] = (
    "mass_send", "mass_send_v2", "auto_reply", "ai_chat", "ai_clone",
    "ai_comment", "ai_script", "copy_chat", "batch_invite", "batch_invite_v2",
    "batch_join", "batch_rename", "forwarder", "add_contact",
)

__all__ = [
    "BOT_RESULTS_EMPTY",
    "CLIENT_NOT_AWAITED",
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
    "SESSION_BUSY",
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
    items: list[str] = field(default_factory=list)
    #: ``(message id, text)`` of every item, in the same order as ``items``.
    #: A page that has to answer *in* a post (ai_comment) needs the id, and the
    #: string form above deliberately carries no id so a task file can feed it.
    posts: list[tuple[int, str]] = field(default_factory=list)
    #: Per-post flags the capture could see: ``forwarded`` when the post carries
    #: a forward header at all, ``forwarded_channel`` when that header points at
    #: a channel (the reference's "channel-forwarded root").
    post_flags: dict[int, tuple[str, ...]] = field(default_factory=dict)


@dataclass(slots=True)
class Content:
    """What goes out: text, an optional media path and an optional forward."""

    text: str = ""
    media: str = ""
    forward_from: str = ""
    sticker: str = ""
    #: Answer inside one thread (a channel post id), instead of a plain post.
    reply_to: int = 0
    #: The page's "disable link preview": the text goes out as it is, a URL in
    #: it stays a URL and is not expanded into a card.
    no_preview: bool = False

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
        if self.reply_to:
            parts.append(f"reply_to={self.reply_to}")
        return " ".join(parts) or "(empty)"


#: What Telethon says when it cannot turn a peer into an input entity.  Only
#: these failures may be retried with the other form of the same target: the
#: message never left, so a retry cannot double send.
_UNRESOLVED = (
    "cannot find any entity",
    "could not find the input entity",
    "peer id invalid",
    "user_id_invalid",
    "no user has",
)


def _unresolved(error: BaseException) -> bool:
    if type(error).__name__ in ("ValueError", "PeerIdInvalidError",
                                "UserIdInvalidError"):
        return True
    lowered = str(error).lower()
    return any(marker in lowered for marker in _UNRESOLVED)


def peer_variants(target: Target) -> list[Any]:
    """The forms to try for one target, best first.

    A numeric id has to go in as ``int``: Telethon parses any digit *string*
    as a phone number, which is how ``--targets 6805350975`` used to end up
    asking for ``+6805350975``.  The phone form stays as the fallback for a
    number that really is a phone number written without a plus.

    A private-group id from a ``t.me/c/<id>`` link names a *channel* peer,
    not a user: Telethon turns a bare ``int`` into ``PeerUser`` and then
    fails with "could not find the input entity for PeerUser" even when the
    account sits in the group.  The ``PeerChannel`` form resolves the moment
    the account has the dialog, so it leads and the plain int stays behind
    for the number cases.
    """
    ref = target.ref
    if ref.isdigit():
        forms: list[Any] = [int(ref)]
        if target.kind == "channel":
            from telethon.tl.types import PeerChannel

            forms.insert(0, PeerChannel(int(ref)))
        if target.phone_fallback:
            forms.append(target.phone_fallback)
        return forms
    return [ref]


def _kind_of(entity: Any) -> str:
    """What a resolved peer is, in the buckets the sort page keeps.

    A username resolves to anything -- a user, a broadcast channel, a group
    that only takes requests -- and the page sorts on that answer: reporting
    every one of them as "can send" is how a channel ends up in the wrong list.
    """
    if entity.__class__.__name__ == "User":
        return "USER"
    if getattr(entity, "broadcast", False) and not getattr(entity, "megagroup",
                                                           False):
        return "CHANNEL"
    if getattr(entity, "join_request", False):
        return "JOIN_REQUEST"
    rights = (getattr(entity, "default_banned_rights", None)
              or getattr(entity, "banned_rights", None))
    if rights is not None and getattr(rights, "send_messages", False):
        return CHAT_WRITE_FORBIDDEN
    return ""


def _resolve_link(client: Any, target: Target) -> SendResult:
    """What a link says about its group, read only.

    A link is not an address, so nothing can look it up as a peer: the hash has
    to be checked instead.  ``CheckChatInviteRequest`` only reads -- it joins
    nothing and requests nothing -- which is exactly why a classification page
    may ask it and may not use ``ImportChatInviteRequest``.
    """
    from telethon.tl.functions.messages import CheckChatInviteRequest
    from telethon.tl.types import ChatInvite

    checked = client(CheckChatInviteRequest(target.invite_hash))
    chat = getattr(checked, "chat", None)
    face = chat if chat is not None else checked
    title = str(getattr(face, "title", "") or "")
    count = int(getattr(face, "participants_count", 0) or 0)
    detail = title or target.ref
    if chat is not None:
        # Already a member: the answer carries the chat itself.
        return SendResult(ok=True, count=count, detail=detail)
    if isinstance(checked, ChatInvite):
        # Not joined: the server still shows the group's face, which is all a
        # sort needs, and says whether it is a channel or needs approval.
        if getattr(checked, "broadcast", False):
            return SendResult(ok=True, code="CHANNEL", count=count,
                              detail=detail)
        if getattr(checked, "request_needed", False):
            return SendResult(ok=True, code=JOIN_REQUEST, count=count,
                              detail=detail)
        return SendResult(ok=True, count=count, detail=detail)
    return SendResult(ok=False, code=TARGET_INVALID,
                      detail="the link answered with nothing readable")


def _is_already_joined(error: BaseException) -> bool:
    """Importing a link we are already a member of answers with these."""
    return type(error).__name__ in ("UserAlreadyParticipantError",
                                    "InviteRequestSentError")


def chat_entity(client: Any, target: Target) -> Any:
    """The chat behind a target, joining a private link first when needed.

    This build of Telethon ships no contacts/join/invite helpers, so a private
    group is only reachable through the invite hash.  ``CheckChatInviteRequest``
    answers for members *and* strangers alike -- it returns the chat itself once
    we are already in -- so it goes first; importing is only for a link we have
    not joined yet.  Without that order an already-joined group falls through to
    the phone fallback and dies as "cannot find any entity to +<hash>".
    """
    if target.is_invite():
        from telethon.tl.functions.messages import (
            CheckChatInviteRequest,
            ImportChatInviteRequest,
        )

        preview = client(CheckChatInviteRequest(target.invite_hash))
        chat = getattr(preview, "chat", None)
        if chat is not None:
            return chat
        try:
            updates = client(ImportChatInviteRequest(target.invite_hash))
        except Exception as error:  # noqa: BLE001 - already joined is not a fault
            if not _is_already_joined(error):
                raise
        else:
            chats = getattr(updates, "chats", None) or []
            if chats:
                return chats[0]
    last: Exception | None = None
    for peer in peer_variants(target):
        try:
            return client.get_entity(peer)
        except Exception as error:  # noqa: BLE001 - the next form may resolve
            if not _unresolved(error):
                raise
            last = error
    # A bare user id carries no access hash, so a session that has not listed
    # its chats yet cannot resolve one and reports a live user as invalid.
    # One dialog listing seeds the cache; anything else would be a guess.
    if _bare_id(target):
        client.get_dialogs(limit=None)
        for peer in peer_variants(target):
            try:
                return client.get_entity(peer)
            except Exception as error:  # noqa: BLE001 - retried once above
                if not _unresolved(error):
                    raise
                last = error
    assert last is not None
    raise last


def _bare_id(target: Target) -> bool:
    """True when the target is only a number: a user id, not a username."""
    return bool(str(getattr(target, "ref", "") or "").lstrip("-").isdigit())


def user_input(client: Any, target: Target) -> Any:
    """An ``InputUser`` for a target, trying each workable form in turn."""
    last: Exception | None = None
    for peer in peer_variants(target):
        try:
            return client.get_input_entity(peer)
        except Exception as error:  # noqa: BLE001 - the next form may resolve
            if not _unresolved(error):
                raise
            last = error
    if _bare_id(target):
        client.get_dialogs(limit=None)
        return client.get_input_entity(peer_variants(target)[0])
    assert last is not None
    raise last


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


def _code_from_class(name: str) -> str:
    """``InviteHashExpiredError`` -> ``INVITE_HASH_EXPIRED``.

    Telethon names its error classes after the server's answer, but the text of
    the exception is a human sentence that usually does not repeat the code
    (``The chat the user tried to join has expired...``), so matching the class
    name is the only reliable read.
    """
    base = name[:-5] if name.endswith("Error") else name
    return re.sub(r"(?<!^)(?=[A-Z])", "_", base).upper()


def code_from_exception(error: BaseException) -> tuple[str, str, bool]:
    """Map a Telethon error to one of the exported codes."""
    name = type(error).__name__
    text = str(error)
    upper = text.upper()
    if name == "ClientNotAwaited":
        return CLIENT_NOT_AWAITED, text, False
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
    # Two ogtool processes cannot share one Telethon session: the .session file
    # is SQLite too, and the loser gets 'database is locked'. Saying so beats
    # 'UNKNOWN: OperationalError', which reads like a Telegram problem.
    if "DATABASE IS LOCKED" in upper or "SESSION IS LOCKED" in upper:
        return SESSION_BUSY, "another ogtool process is using this session", True
    if "PHONE_NUMBER_OCCUPIED" in upper or "PHONE_OCCUPIED" in upper:
        return PHONE_OCCUPIED, text, False
    # Last resort: the class name itself is the answer, which is how the errors
    # whose message is a sentence (expired invite, banned, admin required) get
    # their real code instead of UNKNOWN.
    by_class = _code_from_class(name)
    if by_class in KNOWN_CODES:
        return by_class, text, by_class in RETRYABLE
    return UNKNOWN, f"{name}: {text}", False


class ClientNotAwaited(RuntimeError):
    """An awaitable came back where a value should be: nothing was sent."""


class _AwaitedClient:
    """The client, with every call checked for an un-unawaited coroutine.

    ``telethon.TelegramClient`` (the async one) returns coroutines; the sync
    wrapper this module uses returns real values.  If the import ever slides
    back, every call would look like a success while doing nothing -- exactly
    the bug this class exists to make impossible to miss.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._client, name)
        if not callable(attribute):
            return attribute

        def call(*args: Any, **kwargs: Any) -> Any:
            return self._checked(attribute(*args, **kwargs), name)

        return call

    def __call__(self, request: Any) -> Any:
        return self._checked(self._client(request), "request")

    @staticmethod
    def _checked(value: Any, name: str) -> Any:
        if not inspect.isawaitable(value):
            return value
        if inspect.iscoroutine(value):
            value.close()
        raise ClientNotAwaited(f"{name} returned a coroutine; the async client "
                               f"was used without await")


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
        #: Answers a task file supplies because a dry run cannot ask Telegram:
        #: ``contacts`` maps an account to its contact list, ``activity`` maps a
        #: chat to the hours since its last message.
        self.contacts: dict[str, list[str]] = {}
        self.activity: dict[str, float] = {}
        self.created: dict[str, str] = {}

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

    def react(self, account: str, target: Target, message_id: int,
              emoji: str = "\U0001F44D") -> SendResult:
        self.calls.append(("react", f"{account}:{target.ref}#{message_id}"))
        return SendResult(ok=True)

    def send_sticker(self, account: str, target: Target, sets: list[str],
                     reply_to: int = 0, emoji: str = "") -> SendResult:
        """A sticker from the enabled sets; a dry run sends nothing."""
        self.calls.append(("sticker", f"{account}:{target.ref}"))
        if not sets:
            return SendResult(ok=False, code="STICKER_SET_MISSING",
                              detail="no sticker set is enabled")
        return SendResult(ok=True,
                          detail=f"set={random.choice(sets)}"
                                 + (f" emoji={emoji}" if emoji else ""))

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

    def mute(self, account: str, target: Target, until: int = 0) -> SendResult:
        return self._answer("mute", account,
                            f"target={target.display()} until={until or 'forever'}")

    def chat_count(self, account: str) -> SendResult:
        """How many chats the account is in; a dry run counts zero."""
        result = self._answer("chat_count", account, "dialogs")
        result.count = 0
        return result

    def pull_contacts(self, account: str) -> SendResult:
        """The account's own contact list, the pull stage's only job."""
        return self._answer("pull_contacts", account, "own contacts")

    def start_bot(self, account: str, bot: str, param: str = "") -> SendResult:
        """Open a bot link, with its ``start`` parameter when there is one."""
        return self._answer("start_bot", account,
                            f"bot=@{bot} param={param or '-'}")

    def request_mini_app(self, account: str, bot: str, param: str = "",
                         app: str = "") -> SendResult:
        """Trigger a mini app, the ``startapp`` half of the same link."""
        return self._answer("mini_app", account,
                            f"bot=@{bot} app={app or '-'} param={param or '-'}")

    def join_call(self, account: str, target: Target,
                  muted: bool = False) -> SendResult:
        """Enter the group call, muted or speaking, like the page's two actions."""
        return self._answer(
            "call", account,
            f"target={target.display()} muted={1 if muted else 0}")

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

    def update_about(self, account: str, text: str) -> SendResult:
        return self._answer("about", account, f"about={text or '-'}")

    def update_username(self, account: str, username: str) -> SendResult:
        return self._answer("username", account, f"username={username or '-'}")

    def update_avatar(self, account: str, path: str) -> SendResult:
        return self._answer("avatar", account, f"path={path or '-'}")

    def add_contact(self, account: str, target: Target,
                    first: str = "") -> SendResult:
        return self._answer("contact", account,
                            f"target={target.display()} first={first or '-'}")

    def resolve(self, account: str, target: Target) -> SendResult:
        result = self._answer("resolve", account, f"target={target.display()}")
        if result.ok:
            result.detail = target.title or target.ref
        return result

    def invite_link(self, account: str, target: Target) -> SendResult:
        """A dry run writes no link: exporting one needs a real session."""
        return self._answer("invite_link", account,
                            f"target={target.display()}")

    def set_proxy(self, account: str, proxy: str) -> SendResult:
        return self._answer("proxy", account, f"proxy={proxy or '-'}")

    def logout(self, account: str) -> SendResult:
        return self._answer("logout", account)

    # contacts, groups and activity -----------------------------------

    def get_contacts(self, account: str) -> SendResult:
        """The contacts of one account, as ``name|user_id`` strings."""
        result = self._answer("contacts", account,
                              f"count={len(self.contacts.get(account) or [])}")
        if result.ok:
            result.items = list(self.contacts.get(account) or [])
            result.count = len(result.items)
        return result

    def create_group(self, account: str, title: str, about: str = "",
                     megagroup: bool = True) -> SendResult:
        result = self._answer("create_group", account,
                              f"title={title or '-'} megagroup={megagroup}")
        if result.ok:
            kind = "channel" if megagroup else "group"
            result.detail = f"{kind}:{title}"
            self.created[title] = result.detail
        return result

    def last_activity(self, account: str, target: Target) -> SendResult:
        """Hours since the chat's last message; ``-1`` when it looks empty."""
        hours = self.activity.get(target.ref)
        note = f"target={target.display()}"
        result = self._answer("activity", account,
                              f"{note} hours={'-' if hours is None else hours}")
        if result.ok:
            result.count = -1 if hours is None else int(hours)
        return result

    def close(self) -> None:
        """Nothing to release; kept so both backends share one interface."""

    def history(self, account: str, target: Target, minutes: int = 0,
                limit: int = 0) -> SendResult:
        """Source messages a live run would read; offline there are none."""
        result = self._answer(
            "history", account,
            f"target={target.display()} minutes={minutes} limit={limit}")
        if result.ok:
            result.items = []
        return result


def _deliver(client: Any, peer: Any, content: Content) -> Any:
    """One message, whichever shape the content asks for."""
    reply_to = content.reply_to or None
    if content.forward_from:
        return client.forward_messages(peer, content.forward_from)
    if content.sticker and Path(content.sticker).exists():
        return client.send_file(peer, content.sticker,
                                caption=content.text or None)
    if content.media:
        return client.send_file(peer, content.media,
                                caption=content.text or None,
                                reply_to=reply_to)
    return client.send_message(peer, content.text, reply_to=reply_to)


def _save_media(message: Any, folder: str) -> str:
    """Download one message's media into ``folder``; empty when it cannot.

    A failed download must not lose the text that came with it: the caller
    keeps the message either way and only the attachment is missing.
    """
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
        path = message.download_media(file=folder)
    except Exception:  # noqa: BLE001 - the text still gets copied
        return ""
    return str(path or "")


def _sticker_emoji(document: Any) -> str:
    """The emoji a sticker document advertises, empty when it has none."""
    for attribute in getattr(document, "attributes", []) or []:
        emoji = getattr(attribute, "alt", "")
        if type(attribute).__name__ == "DocumentAttributeSticker" and emoji:
            return str(emoji)
    return ""


def _forward_flags(message: Any) -> tuple[str, ...]:
    """``forwarded`` / ``forwarded_channel`` for one post, from its fwd header.

    The reference's ai_comment page has a switch labelled "Only comment
    channel-forwarded root messages", so the capture has to be able to say
    whether a post came from a channel at all - the text of a forwarded post is
    identical to a typed one, and guessing from the text would be a lie.
    """
    header = getattr(message, "fwd_from", None)
    if header is None:
        return ()
    if getattr(header, "channel_post", None) is not None:
        return ("forwarded", "forwarded_channel")
    author = getattr(header, "from_id", None)
    if type(author).__name__ == "PeerChannel":
        return ("forwarded", "forwarded_channel")
    return ("forwarded",)


def _sender_ref(message: Any) -> str:
    """Who said it: the username when there is one, else the numeric id."""
    sender = getattr(message, "sender", None)
    username = getattr(sender, "username", "") or ""
    if username:
        return f"@{username}"
    return str(getattr(message, "sender_id", 0) or 0)


def _proxy_tuple(url: str) -> Any:
    """``socks5://user:pass@host:1080`` -> the tuple Telethon accepts."""
    text = (url or "").strip()
    if not text:
        return None
    parts = urlsplit(text)
    kind = (parts.scheme or "socks5").lower()
    if kind not in ("socks5", "socks4"):
        raise ValueError(f"unsupported proxy kind: {kind}")
    if not parts.hostname:
        raise ValueError("proxy URL has no host")
    return (kind, parts.hostname, parts.port or 1080, True,
            parts.username or None, parts.password or None)


@dataclass(slots=True)
class TelethonSender:
    """The real backend. Imports Telethon lazily, so dry runs need no install."""

    settings: Settings
    live: bool = True
    _clients: dict[str, Any] = field(default_factory=dict)
    _client_lock: Any = field(default_factory=threading.RLock, repr=False)
    #: Resolved group calls, per (account, target ref). ``JoinGroupCallRequest``
    #: needs the call itself, and the page's actions repeat on the same group;
    #: caching here is what "success cached group call slug" actually meant -
    #: the page used to keep a synthesized string nobody consumed.
    _calls: dict[tuple[str, str], Any] = field(default_factory=dict)

    def _client(self, account: str) -> Any:
        # Two lanes may reach for their first client at the same moment; the
        # lock keeps one account from being connected twice.
        with self._client_lock:
            return self._client_locked(account)

    def _client_locked(self, account: str) -> Any:
        if account in self._clients:
            return self._clients[account]
        api_id, api_hash = self.settings.require_credentials()
        session_path = self.settings.session_path(account)
        if not session_path.exists():
            raise RuntimeError(f"{SESSION_NOT_FOUND}: {session_path}")
        try:
            # The sync wrapper is the whole point: the async client returns
            # coroutines, so every call would be a silent no-op.
            from telethon.sync import TelegramClient
        except ImportError as error:  # pragma: no cover - live only
            raise RuntimeError("telethon is not installed") from error
        proxy = self.settings.proxy.to_telethon() if self.settings.proxy else None
        client = _AwaitedClient(
            TelegramClient(str(session_path), api_id, api_hash, proxy=proxy)
        )
        try:
            client.connect()
        except Exception:
            # A half opened client keeps Telethon's send/receive loops pending
            # and they only show up as "Task was destroyed" at interpreter
            # exit; closing it here is the only place both are still known.
            with contextlib.suppress(Exception):
                client.disconnect()
            raise
        self._clients[account] = client
        return client

    def invite_link(self, account: str, target: Target) -> SendResult:
        """``ensureInviteLinkSnapshot``: one usable link per group.

        ``ExportChatInviteRequest`` returns an existing link when the account may
        export one, and raises when it may not - which is exactly the answer the
        page wants to record.
        """

        def action() -> SendResult:
            from telethon.tl.functions.messages import ExportChatInviteRequest

            client = self._client(account)
            peer = self._first_peer(account, target)
            result = client(ExportChatInviteRequest(peer=peer))
            answer = SendResult(ok=True)
            answer.detail = str(getattr(result, "link", "") or "")
            return answer

        return self._guard("invite_link", account, action)

    def _first_peer(self, account: str, target: Target) -> Any:
        for variant in peer_variants(target):
            try:
                return self._client(account).get_input_entity(variant)
            except Exception:  # noqa: BLE001 - try the next form
                continue
        raise ValueError(f"cannot resolve {target.display()}")

    def _guard(self, operation: str, account: str, action: Any) -> SendResult:
        try:
            return action()
        except Exception as error:  # noqa: BLE001 - mapped below
            code, detail, retryable = code_from_exception(error)
            return SendResult(ok=False, code=code, detail=detail,
                              retryable=retryable)

    def _on_target(self, account: str, target: Target, operation: str,
                   call: Any) -> SendResult:
        """Run ``call(client, peer)`` for the first peer form that resolves."""

        def action() -> Any:
            client = self._client(account)
            last: Exception | None = None
            for peer in peer_variants(target):
                try:
                    return call(client, peer)
                except Exception as error:  # noqa: BLE001 - retried below
                    if not _unresolved(error):
                        raise
                    last = error
            assert last is not None
            raise last

        return self._guard(operation, account, action)

    def react(self, account: str, target: Target, message_id: int,
              emoji: str = "\U0001F44D") -> SendResult:
        """One reaction on one message: an account may add at most one."""

        def call(client: Any, peer: Any) -> SendResult:
            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl.types import ReactionEmoji

            if not message_id:
                return SendResult(ok=False, code=TARGET_INVALID,
                                  detail="a reaction needs a message id")
            client(SendReactionRequest(
                peer=peer, msg_id=int(message_id),
                reaction=[ReactionEmoji(emoticon=emoji)]))
            return SendResult(ok=True, message_id=int(message_id))

        return self._on_target(account, target, "react", call)

    def send(self, account: str, target: Target, content: Content) -> SendResult:
        def call(client: Any, peer: Any) -> SendResult:
            message = _deliver(client, peer, content)
            return SendResult(ok=True,
                              message_id=int(getattr(message, "id", 0)))

        return self._on_target(account, target, "send", call)

    def ping(self, account: str) -> SendResult:
        """Ask the server for our own user, which also refreshes our presence."""

        def action() -> SendResult:
            client = self._client(account)
            if not client.is_user_authorized():
                return SendResult(ok=False, code=SESSION_NOT_FOUND,
                                  detail="the session is not authorized")
            me = client.get_me()
            if me is None:
                return SendResult(ok=False, code=SESSION_NOT_FOUND,
                                  detail="get_me() returned nothing")
            return SendResult(ok=True, detail=str(getattr(me, "id", "")))

        return self._guard("ping", account, action)

    def join(self, account: str, target: Target) -> SendResult:
        """Join a group by address or by private invite link."""

        def action() -> SendResult:
            from telethon.tl.functions.channels import JoinChannelRequest
            from telethon.tl.functions.messages import ImportChatInviteRequest

            client = self._client(account)
            if target.is_invite():
                try:
                    client(ImportChatInviteRequest(target.invite_hash))
                except Exception as error:  # noqa: BLE001 - joined already
                    if type(error).__name__ == "InviteRequestSentError":
                        return SendResult(ok=True, code=JOIN_REQUEST,
                                          detail="the group needs an approval")
                    if not _is_already_joined(error):
                        raise
                    return SendResult(ok=True,
                                      code=USER_ALREADY_PARTICIPANT)
                return SendResult(ok=True)
            chat = chat_entity(client, target)
            try:
                client(JoinChannelRequest(chat))
            except Exception as error:  # noqa: BLE001 - joined already
                if not _is_already_joined(error):
                    raise
                return SendResult(ok=True, code=USER_ALREADY_PARTICIPANT)
            return SendResult(ok=True)

        return self._guard("join", account, action)

    def leave(self, account: str, target: Target) -> SendResult:
        """Leave a channel or a plain group, whichever the target is."""

        def action() -> SendResult:
            from telethon.tl.functions.channels import LeaveChannelRequest
            from telethon.tl.functions.messages import DeleteChatUserRequest
            from telethon.tl.types import Channel

            client = self._client(account)
            chat = chat_entity(client, target)
            if isinstance(chat, Channel):
                client(LeaveChannelRequest(chat))
            else:
                me = client.get_input_entity("me")
                client(DeleteChatUserRequest(chat_id=chat.id, user_id=me))
            return SendResult(ok=True)

        return self._guard("leave", account, action)

    def mute(self, account: str, target: Target, until: int = 0) -> SendResult:
        """Silence one chat, the way the join page's mute switches ask for.

        ``mute_until`` is the server's own field: ``0x7FFFFFFF`` is "forever",
        which is what a mutate-after-join wants.
        """

        def action() -> SendResult:
            from telethon.tl.functions.account import UpdateNotifySettingsRequest
            from telethon.tl.types import InputNotifyPeer, InputPeerNotifySettings

            client = self._client(account)
            peer = chat_entity(client, target)
            settings = InputPeerNotifySettings(mute_until=until or 0x7FFFFFFF)
            client(UpdateNotifySettingsRequest(
                peer=InputNotifyPeer(peer), settings=settings))
            return SendResult(ok=True)

        return self._guard("mute", account, action)

    def chat_count(self, account: str) -> SendResult:
        """The account's dialog count, for ``maxJoinedChatsPerAccount``.

        ``GetDialogsRequest(limit=0)`` returns only ``count`` - the cheap way to
        ask "how many chats is this account in" without paging the list.
        """

        def action() -> SendResult:
            from telethon.tl.functions.messages import GetDialogsRequest
            from telethon.tl.types import InputPeerEmpty

            client = self._client(account)
            result = client(GetDialogsRequest(
                offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(),
                limit=0, hash=0))
            answer = SendResult(ok=True)
            answer.count = int(getattr(result, "count", 0) or 0)
            return answer

        return self._guard("chat_count", account, action)

    def pull_contacts(self, account: str) -> SendResult:
        """``contacts.GetContactsRequest``: the account's own address book.

        The result rides on ``detail`` as ``user|handle`` lines, because that is
        what the next stage consumes and inventing a second return shape for one
        caller would make the sender interface lie about itself.
        """

        def action() -> SendResult:
            from telethon.tl.functions.contacts import GetContactsRequest

            client = self._client(account)
            found = client(GetContactsRequest(hash=0))
            rows: list[str] = []
            for user in getattr(found, "users", []) or []:
                handle = getattr(user, "username", "") or ""
                name = " ".join(part for part in (
                    getattr(user, "first_name", "") or "",
                    getattr(user, "last_name", "") or "") if part).strip()
                rows.append(f"{name or '-'}|@{handle}" if handle
                            else f"{name or '-'}|{getattr(user, 'id', '')}")
            return SendResult(ok=True, detail="\n".join(rows))

        return self._guard("pull_contacts", account, action)

    def start_bot(self, account: str, bot: str, param: str = "") -> SendResult:
        """``messages.StartBotRequest``: the same call a ``t.me/bot?start=`` link makes.

        The reference's own log line for this is "silently started bot %2" -
        silent because the bot's answer is not read, only the start is sent.
        """

        def action() -> SendResult:
            import os

            from telethon.tl.functions.messages import StartBotRequest

            client = self._client(account)
            peer = client.get_input_entity("me")
            client(StartBotRequest(bot=bot, peer=peer,
                                   random_id=int.from_bytes(
                                       os.urandom(8), "big", signed=True),
                                   start_param=param or ""))
            return SendResult(ok=True)

        return self._guard("start_bot", account, action)

    def request_mini_app(self, account: str, bot: str, param: str = "",
                         app: str = "") -> SendResult:
        """A ``startapp`` link is a start with the app name in the parameter.

        Telegram has one entry point for both, so this reuses ``start_bot`` and
        documents that the app name travels in the same ``start_param`` slot.
        """
        combined = param or app
        if app and param:
            combined = f"{app}___{param}"
        return self.start_bot(account, bot, combined)

    def join_call(self, account: str, target: Target,
                  muted: bool = False) -> SendResult:
        """Join the group call, then set the microphone state.

        ``JoinGroupCallRequest`` needs the call itself, which is a
        ``phone.getGroupCall``-shaped lookup; the join is done first and the mic
        is set on the participant entry, because joining unmuted is what the
        page's "Speak" does while "Mic off" joins muted.
        """

        def action() -> SendResult:
            from telethon.tl.functions.phone import (
                EditGroupCallParticipantRequest,
                JoinGroupCallRequest,
            )
            from telethon.tl.types import (
                DataJSON,
                PhoneCall,
            )

            client = self._client(account)
            chat = chat_entity(client, target)
            key = (account, target.ref)
            call: PhoneCall | None = self._calls.get(key)
            if call is None:
                call = self._resolve_call(client, chat)
                if call is not None:
                    self._calls[key] = call
            if call is None:
                return SendResult(ok=False, code="GROUP_CALL_NOT_FOUND",
                                  detail="no group call on this chat")
            # The params carry this client's WebRTC SSRC. An empty "{}" joins
            # once and then the server starts answering
            # GROUPCALL_SSRC_DUPLICATE_MUCH: every attempt must present a
            # fresh ssrc, and a rejected attempt must retry with a new one.
            for attempt in range(4):
                ssrc = random.randrange(1, 2**31 - 1)
                try:
                    client(JoinGroupCallRequest(
                        call=call, join_as="me",
                        params=DataJSON(data=json.dumps({"ssrc": ssrc})),
                        muted=muted, video_stopped=True))
                    break
                except Exception as error:  # noqa: BLE001 - mapped below
                    if "SSRC_DUPLICATE" not in str(error).upper() \
                            or attempt == 3:
                        raise
            if not muted:
                client(EditGroupCallParticipantRequest(
                    call=call, participant="me", muted=False))
            return SendResult(ok=True)

        return self._guard("call", account, action)

    def _resolve_call(self, client: Any, chat: Any) -> Any:
        """The group call behind a chat, or ``None`` when it has none."""
        from telethon.tl.functions.channels import GetFullChannelRequest

        try:
            resolved = client(GetFullChannelRequest(chat))
        except Exception:  # noqa: BLE001 - the call may simply not exist
            return None
        call_holder = getattr(getattr(resolved, "full_chat", None), "call",
                              None)
        if call_holder is None:
            return None
        return getattr(call_holder, "call", None) or call_holder

    def invite(self, account: str, group: Target, member: Target) -> SendResult:
        """Add one user to a group, joining a private link first if needed."""

        def action() -> SendResult:
            from telethon.tl.functions.channels import InviteToChannelRequest

            client = self._client(account)
            chat = chat_entity(client, group)
            user = user_input(client, member)
            client(InviteToChannelRequest(channel=chat, users=[user]))
            return SendResult(ok=True)

        return self._guard("invite", account, action)

    def get_participants(self, account: str, target: Target,
                         limit: int = 0) -> SendResult:
        def call(client: Any, peer: Any) -> SendResult:
            users = client.get_participants(peer, limit=limit or None)
            return SendResult(ok=True, count=len(users))

        return self._on_target(account, target, "participants", call)

    def update_profile(self, account: str, first: str,
                       last: str = "") -> SendResult:
        def action() -> SendResult:
            from telethon.tl.functions.account import UpdateProfileRequest

            client = self._client(account)
            client(UpdateProfileRequest(first_name=first or None,
                                        last_name=last or None))
            return SendResult(ok=True)

        return self._guard("profile", account, action)

    def update_about(self, account: str, text: str) -> SendResult:
        def action() -> SendResult:
            from telethon.tl.functions.account import UpdateProfileRequest

            client = self._client(account)
            # ``about`` rides on UpdateProfileRequest since layer 118; the bio
            # is the same field the reference's "bio" strings mean.
            client(UpdateProfileRequest(about=text or None))
            return SendResult(ok=True)

        return self._guard("about", account, action)

    def update_username(self, account: str, username: str) -> SendResult:
        def action() -> SendResult:
            from telethon.tl.functions.account import UpdateUsernameRequest

            client = self._client(account)
            client(UpdateUsernameRequest(username=username or ""))
            return SendResult(ok=True)

        return self._guard("username", account, action)

    def update_avatar(self, account: str, path: str) -> SendResult:
        def action() -> SendResult:
            from telethon.tl.functions.photos import (
                UploadProfilePhotoRequest,
            )

            client = self._client(account)
            uploaded = client.upload_file(path)
            client(UploadProfilePhotoRequest(file=uploaded))
            return SendResult(ok=True)

        return self._guard("avatar", account, action)

    def add_contact(self, account: str, target: Target,
                    first: str = "") -> SendResult:
        def action() -> SendResult:
            from telethon.tl.functions.contacts import AddContactRequest

            client = self._client(account)
            user = user_input(client, target)
            phone = target.ref if target.ref.startswith("+") else ""
            client(AddContactRequest(
                id=user,
                first_name=first or target.display(),
                last_name="",
                phone=phone,
                add_phone_privacy_exception=False,
            ))
            return SendResult(ok=True)

        return self._guard("contact", account, action)

    def resolve(self, account: str, target: Target) -> SendResult:
        def action() -> SendResult:
            client = self._client(account)
            if target.is_invite():
                return _resolve_link(client, target)
            last: Exception | None = None
            for peer in peer_variants(target):
                try:
                    entity = client.get_entity(peer)
                except Exception as error:  # noqa: BLE001 - retried below
                    if not _unresolved(error):
                        raise
                    last = error
                    continue
                title = (getattr(entity, "title", "")
                         or getattr(entity, "username", ""))
                count = int(getattr(entity, "participants_count", 0) or 0)
                return SendResult(ok=True, code=_kind_of(entity),
                                  detail=str(title or target.ref),
                                  count=count)
            # Same bare-id problem as chat_entity: one dialog listing turns a
            # user id into an entity, and without it a live user reads invalid.
            if _bare_id(target):
                client.get_dialogs(limit=None)
                for peer in peer_variants(target):
                    try:
                        entity = client.get_entity(peer)
                    except Exception as error:  # noqa: BLE001 - retried once
                        if not _unresolved(error):
                            raise
                        last = error
                        continue
                    title = (getattr(entity, "title", "")
                             or getattr(entity, "username", ""))
                    return SendResult(
                        ok=True, code=_kind_of(entity),
                        detail=str(title or target.ref),
                        count=int(getattr(entity,
                                          "participants_count", 0) or 0))
            assert last is not None
            raise last

        return self._guard("resolve", account, action)

    def set_proxy(self, account: str, proxy: str) -> SendResult:
        def action() -> SendResult:
            client = self._client(account)
            client.set_proxy(_proxy_tuple(proxy))
            return SendResult(ok=True)

        return self._guard("proxy", account, action)

    def get_contacts(self, account: str) -> SendResult:
        def action() -> SendResult:
            from telethon.tl.functions.contacts import GetContactsRequest

            client = self._client(account)
            answer = client(GetContactsRequest(hash=0))
            items: list[str] = []
            for user in getattr(answer, "users", None) or []:
                name = " ".join(
                    part for part in (getattr(user, "first_name", ""),
                                      getattr(user, "last_name", "")) if part
                )
                username = getattr(user, "username", "") or ""
                ref = f"@{username}" if username else str(getattr(user, "id", ""))
                items.append(f"{name or ref}|{ref}")
            return SendResult(ok=True, items=items, count=len(items))

        return self._guard("contacts", account, action)

    def create_group(self, account: str, title: str, about: str = "",
                     megagroup: bool = True) -> SendResult:
        def action() -> SendResult:
            from telethon.tl.functions.channels import CreateChannelRequest

            client = self._client(account)
            created = client(CreateChannelRequest(
                title=title, about=about, megagroup=megagroup,
            ))
            entity = created.chats[0]
            username = getattr(entity, "username", "") or ""
            ref = f"@{username}" if username else str(getattr(entity, "id", ""))
            return SendResult(ok=True, detail=ref, count=int(entity.id))

        return self._guard("create_group", account, action)

    def last_activity(self, account: str, target: Target) -> SendResult:
        def call(client: Any, peer: Any) -> SendResult:
            from datetime import UTC, datetime

            messages = client.get_messages(peer, limit=1)
            if not messages:
                return SendResult(ok=True, count=-1, detail="no messages")
            stamp = getattr(messages[0], "date", None)
            if stamp is None:
                return SendResult(ok=True, count=-1, detail="no date")
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            hours = (datetime.now(UTC) - stamp).total_seconds() / 3600.0
            return SendResult(ok=True, count=int(hours),
                              detail=f"last message {int(hours)}h ago")

        return self._on_target(account, target, "activity", call)

    def logout(self, account: str) -> SendResult:
        def action() -> SendResult:
            client = self._client(account)
            done = client.log_out()
            if not done:
                return SendResult(ok=False, code=UNKNOWN,
                                  detail="log_out() returned False")
            self._clients.pop(account, None)
            return SendResult(ok=True)

        return self._guard("logout", account, action)

    def history(self, account: str, target: Target, minutes: int = 0,
                limit: int = 0, media_dir: str = "") -> SendResult:
        """The recent messages of one chat, oldest first, as source strings.

        Each item is ``peer|sender|text``, the shape ``sources.parse_many``
        reads, so a live capture and a task file feed the same parser.  A
        caption is flattened to one line and ``|`` becomes ``/``: those two
        characters are the record separator of that format.

        With ``media_dir`` the photo or document is downloaded and written to
        that directory, and the item grows a fourth field holding its path --
        the format's optional media field.  That is what turns a capture into
        something a copy page can replay with the pictures still attached.  A
        post with neither text nor media is still skipped, so ``count``
        (scanned) can be larger than the number of items returned.
        """

        def call(client: Any, peer: Any) -> SendResult:
            from datetime import UTC, datetime, timedelta

            # ``minutes`` is a window that ends now, not an offset into the
            # past: iterate newest first and stop at the first older message.
            # Passing it as ``offset_date`` would read everything *before* that
            # point -- the opposite set, and on a busy chat a 200 message replay.
            cutoff = (datetime.now(UTC) - timedelta(minutes=minutes)
                      if minutes > 0 else None)
            scanned = 0
            captured: list[tuple[int, str, str, str, tuple[str, ...]]] = []
            for message in client.iter_messages(peer, limit=limit or None):
                stamp = getattr(message, "date", None)
                if cutoff is not None and stamp is not None:
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=UTC)
                    if stamp < cutoff:
                        break
                scanned += 1
                body = " ".join((getattr(message, "message", "") or "").split())
                body = body.replace("|", "/")
                saved = ""
                if media_dir and getattr(message, "media", None) is not None:
                    saved = _save_media(message, media_dir)
                flags = _forward_flags(message)
                if body or saved:
                    captured.append((int(message.id), _sender_ref(message),
                                     body, saved, flags))
            captured.reverse()  # Telethon yields newest first
            return SendResult(
                ok=True, count=scanned,
                items=[f"{target.ref}|{sender}|{body}"
                       + (f"|{saved}" if saved else "")
                       + (f"|flags={','.join(flags)}" if flags else "")
                       for _mid, sender, body, saved, flags in captured],
                posts=[(mid, body) for mid, _sender, body, _saved, _f
                       in captured],
                post_flags={mid: flags
                            for mid, _sender, _body, _saved, flags in captured
                            if flags},
            )

        return self._on_target(account, target, "history", call)

    def close(self) -> None:
        for client in self._clients.values():
            with contextlib.suppress(Exception):  # closing must not raise
                client.disconnect()
        self._clients.clear()
