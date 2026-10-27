"""Injected server answers, so every branch can be exercised without a network.

A task file may carry a list of codes::

    simulate_responses:
      - ok
      - PEER_FLOOD
      - FLOOD_WAIT_120

The dry-run transport pops one per request, in order, and answers ``ok`` once
the list is used up.  That is what makes the retry, the backoff, the MISSING
and FLOOD lists and the final report testable offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

OK = "ok"

BOT_RESULTS_EMPTY = "BOT_RESULTS_EMPTY"
CHAT_WRITE_FORBIDDEN = "CHAT_WRITE_FORBIDDEN"
SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
#: Another process holds the same .session file. It is not a Telegram answer, so
#: it must not be confused with a revoked login: the account is fine, it is busy.
SESSION_BUSY = "SESSION_BUSY"
USER_BANNED_IN_CHANNEL = "USER_BANNED_IN_CHANNEL"
PEER_FLOOD = "PEER_FLOOD"
FLOOD_WAIT = "FLOOD_WAIT"
TARGET_INVALID = "TARGET_INVALID"
NETWORK_ERROR = "NETWORK_ERROR"
TIMEOUT = "TIMEOUT"
UNKNOWN = "UNKNOWN"

# Answers the reference client separates into its own result buckets.
USER_PRIVACY_RESTRICTED = "USER_PRIVACY_RESTRICTED"
CHANNEL_PRIVATE = "CHANNEL_PRIVATE"
USER_ALREADY_PARTICIPANT = "USER_ALREADY_PARTICIPANT"
INVITE_HASH_EXPIRED = "INVITE_HASH_EXPIRED"
USER_NOT_MUTUAL_CONTACT = "USER_NOT_MUTUAL_CONTACT"
CHAT_ADMIN_REQUIRED = "CHAT_ADMIN_REQUIRED"
USER_ID_INVALID = "USER_ID_INVALID"
PHONE_OCCUPIED = "PHONE_OCCUPIED"

RETRYABLE = {PEER_FLOOD, FLOOD_WAIT, NETWORK_ERROR, TIMEOUT, BOT_RESULTS_EMPTY}

# Answers that mean "this entry cannot succeed", so it leaves the queue.
TERMINAL = {
    CHAT_WRITE_FORBIDDEN,
    SESSION_BUSY,
    SESSION_NOT_FOUND,
    USER_BANNED_IN_CHANNEL,
    TARGET_INVALID,
    USER_PRIVACY_RESTRICTED,
    CHANNEL_PRIVATE,
    INVITE_HASH_EXPIRED,
    USER_NOT_MUTUAL_CONTACT,
    CHAT_ADMIN_REQUIRED,
    USER_ID_INVALID,
}

# Answers that are not an error but carry the bucket the page sorts them into,
# because the reference client reads that bucket straight off the answer
# (``ACTIVE`` for a live account, ``JOIN_REQUEST`` for a group needing approval).
ACTIVE = "ACTIVE"
CAN_SEND = "CAN_SEND"
JOIN_REQUEST = "JOIN_REQUEST"
CHANNEL = "CHANNEL"
USER = "USER"

CLASSIFIED = {ACTIVE, CAN_SEND, JOIN_REQUEST, CHANNEL, USER}

_WAIT = re.compile(r"^FLOOD_WAIT_(\d+)$")


def normalize(code: str) -> str:
    """Accept ``flood_wait_120`` as well as ``FLOOD_WAIT_120``."""
    text = (code or "").strip()
    return text.upper() if text else OK


def describe(code: str) -> tuple[str, str, bool, float]:
    """``code`` -> (code, detail, retryable, retry_after seconds)."""
    raw = normalize(code)
    if raw in ("", OK, "OK"):
        return OK, "", False, 0.0
    match = _WAIT.match(raw)
    if match:
        seconds = float(match.group(1))
        return FLOOD_WAIT, f"flood wait {seconds:.0f}s", True, seconds
    if raw == "FLOOD_WAIT":
        return FLOOD_WAIT, "flood wait", True, 0.0
    if raw in CLASSIFIED:
        return raw, raw.lower(), False, 0.0
    return raw, raw.lower(), raw in RETRYABLE, 0.0


@dataclass(slots=True)
class Script:
    """An ordered list of answers handed to the dry-run transport."""

    codes: list[str] = field(default_factory=list)
    _index: int = 0

    @classmethod
    def parse(cls, values: object) -> Script:
        if not values:
            return cls([])
        if isinstance(values, str):
            values = [values]
        return cls([str(item) for item in values])  # type: ignore[union-attr]

    def next(self) -> str:
        if self._index < len(self.codes):
            code = self.codes[self._index]
            self._index += 1
            return code
        return OK

    @property
    def used(self) -> int:
        return self._index

    def __len__(self) -> int:
        return len(self.codes)
