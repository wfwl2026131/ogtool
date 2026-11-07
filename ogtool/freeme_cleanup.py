"""The FreeMe group-cleanup engine (FreeMeGroupCleaner, unbound).

The settings page describes the whole machine (:1742835-1742845):

* ``Group cleanup watches remembered target groups and deletes new
  messages through the configured bot token. Worker accounts,
  administrators, group owner, and exception whitelist users are kept;
  join/leave service messages are deleted directly.``
* ``Delete all group messages except worker accounts, administrators,
  group owner, and exception whitelist users. Bot token is required.``
* ``When an external user's message matches group cleanup, permanently
  mute that user in the group. The same user is processed at most once
  every 5 minutes.``
* ``Users in the exception whitelist will not be deleted by group
  cleanup. Enter userId, username, or @username, separated by comma,
  |, or newline.``

The cleaner class family (:2184460-2184493) works through the bot API
(``sendMuteMember(MuteRequest)`` / ``sendDeleteMessage(DeleteRequest)`` /
``fetchChatAdministrators`` :2273950-2273952), and its seven keys live in
the global config (:1740417-1740424: the token, the local-bot switch,
the rate limit, the enable switch, the mute switch, the remembered chat
ids, the admin ids and the whitelist).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

#: The mute window (:1742839): the same user at most once every 5 minutes.
MUTE_DEDUPE_SEC = 300.0

#: Service messages (:1742835): join/leave and their kind are deleted
#: directly, no matter who sent them.
SERVICE_KINDS = ("service", "join", "leave")


def parse_whitelist(text: str) -> list[str]:
    """The whitelist's own separators: comma, pipe, or newline (:1742845).

    ``@name`` keeps its ``@`` so a username and a bare handle stay
    distinct entries; every entry is stripped and empties drop out.
    """
    tokens: list[str] = []
    for line in (text or "").replace("|", ",").splitlines():
        for part in line.split(","):
            token = part.strip()
            if token:
                tokens.append(token)
    return tokens


@dataclass(slots=True)
class CleanerTarget:
    """One message the cleaner has to judge (:1742835)."""

    chat_id: str
    message_id: int
    sender_id: str = ""
    sender_username: str = ""
    is_worker: bool = False
    is_owner: bool = False
    is_service: bool = False
    kind: str = ""


@dataclass(slots=True)
class CleanupConfig:
    """The seven global keys (:1740417-1740424), read as fields."""

    bot_token: str = ""
    use_local_bot: bool = False
    requests_per_second: int = 10
    enabled: bool = False
    mute_external: bool = False
    cached_chat_ids: list[str] = field(default_factory=list)
    admin_user_ids: list[str] = field(default_factory=list)
    whitelist_text: str = ""

    @property
    def whitelist(self) -> list[str]:
        return parse_whitelist(self.whitelist_text)


def delete_verdict(config: CleanupConfig, message: CleanerTarget) -> tuple[bool, str]:
    """Whether one message is deleted, with the page's own reasons.

    The keep set is the page's own (:1742835-1742837): worker accounts,
    administrators (the ``admin_user_ids`` key), the group owner and the
    exception whitelist - a whitelist entry matches a sender by id,
    username or ``@username`` (:1742845).  A service message is deleted
    regardless (:1742835 ``deleted directly``), and the whole engine is
    off until the enable switch is on and a bot token is configured
    (:1742837 ``Bot token is required``).
    """
    if not config.enabled:
        return False, "cleanup disabled"
    if not config.bot_token:
        return False, "Bot token is required"
    if message.is_service or message.kind in SERVICE_KINDS:
        return True, "service message deleted directly"
    whitelist = config.whitelist
    sender = message.sender_username.strip()
    if (
        message.is_worker
        or message.is_owner
        or message.sender_id in config.admin_user_ids
        or message.sender_id in whitelist
        or (sender and sender in whitelist)
        or (sender and f"@{sender.lstrip('@')}" in whitelist)
    ):
        return False, "kept"
    return True, "external message"


def mute_due(last_muted: dict[str, float], sender_id: str, now: float) -> bool:
    """The mute window (:1742839): the same user once per 5 minutes."""
    previous = last_muted.get(sender_id)
    if previous is None or now - previous >= MUTE_DEDUPE_SEC:
        last_muted[sender_id] = now
        return True
    return False


def bot_api_url(token: str, method: str) -> str:
    """The local-bot switch (:1742834) picks the endpoint family."""
    if not token:
        return ""
    return f"https://api.telegram.org/bot{token}/{method}"


def rate_limit_wait(
    sent_log: list[float], requests_per_second: int, now: float
) -> float:
    """``freeMeBotApiRequestsPerSecond`` (:1740419): seconds to wait, 0 = now.

    A per-second cap is a rolling window: every call inside the last
    second counts, and the caller sleeps out the remainder.
    """
    if requests_per_second <= 0:
        return 0.0
    recent = [stamp for stamp in sent_log if now - stamp < 1.0]
    if len(recent) < requests_per_second:
        return 0.0
    oldest = min(recent)
    return max(0.0, 1.0 - (now - oldest))


class GroupCleaner:
    """One round over the remembered chats, judging and acting.

    The engine is the page's own contract; the transport is a seam:
    ``delete`` / ``mute`` / ``administrators`` are callables the caller
    binds to the bot API, so the whole machine is testable without a
    socket.
    """

    def __init__(
        self,
        config: CleanupConfig,
        delete=None,
        mute=None,
        administrators=None,
        clock=time.time,
    ) -> None:
        self.config = config
        self._delete = delete
        self._mute = mute
        self._administrators = administrators
        self._clock = clock
        self._last_muted: dict[str, float] = {}
        self.sent_log: list[float] = []

    def _paced(self) -> None:
        wait = rate_limit_wait(
            self.sent_log, self.config.requests_per_second, self._clock()
        )
        if wait > 0:
            time.sleep(wait)
        self.sent_log.append(self._clock())

    def run(self, messages: list[CleanerTarget]) -> list[tuple[int, str]]:
        """Judge every message; act through the bound seams. ``(id, act)``."""
        acts: list[tuple[int, str]] = []
        if not self.config.enabled or not self.config.bot_token:
            return acts
        admins = set(self.config.admin_user_ids)
        if self._administrators is not None:
            fetched = [str(item) for item in (self._administrators() or [])]
            admins.update(fetched)
        else:
            fetched = []
        live_config = self.config
        if fetched:
            live_config = CleanupConfig(
                bot_token=self.config.bot_token,
                use_local_bot=self.config.use_local_bot,
                requests_per_second=self.config.requests_per_second,
                enabled=self.config.enabled,
                mute_external=self.config.mute_external,
                cached_chat_ids=self.config.cached_chat_ids,
                admin_user_ids=sorted(admins),
                whitelist_text=self.config.whitelist_text,
            )
        for message in messages:
            verdict, reason = delete_verdict(live_config, message)
            if not verdict:
                continue
            self._paced()
            if self._delete is not None:
                self._delete(message.chat_id, message.message_id)
            acts.append((message.message_id, f"deleted ({reason})"))
            if (
                self.config.mute_external
                and not message.is_service
                and message.sender_id
                and message.sender_id not in admins
                and mute_due(self._last_muted, message.sender_id, self._clock())
                and self._mute is not None
            ):
                self._paced()
                self._mute(message.chat_id, message.sender_id)
                acts.append((message.message_id, f"muted ({reason})"))
        return acts


__all__ = [
    "CleanerTarget",
    "CleanupConfig",
    "MUTE_DEDUPE_SEC",
    "GroupCleaner",
    "SERVICE_KINDS",
    "bot_api_url",
    "delete_verdict",
    "mute_due",
    "parse_whitelist",
    "rate_limit_wait",
]
