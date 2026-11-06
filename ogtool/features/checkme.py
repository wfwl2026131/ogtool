"""The checkme post-join verification protocol (CheckmeGroupMgr, unbound).

The reference client polls a small verification service after each join:
``POST <checkmeBaseUrl>/api/__checkme__`` (og-strings.txt :1739935) with the
join facts, and follows the decision the answer carries. This module is that
client: the request keys the reference's own ``CheckmeDecisionClient`` sends
(:1739936-1739957), the full answer vocabulary it reads (:1739958-1739981 -
``trigger`` / ``next`` / ``peer_kind`` / ``can_send_text`` /
``global_mute_text`` / ``bot_captcha`` / ``startapp_payload`` among them),
the six-way decision it obeys (``STOP`` / ``GIVEUP`` / ``WAIT`` /
``NEED_ACTION`` / ``NEED_MANUAL`` / ``UNKNOWN``), the three give-up lines
(max round, expired ``state_token``, a failed action), the passive-observe
and captcha manager lines, and the log lines verbatim.

The service itself is not ours and is never contacted by a dry run: the whole
chain runs only when ``checkme.enabled`` and a ``base_url`` are configured,
which is what ``CheckmeConfig.active`` means.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..config import CheckmeConfig
from ..runtime import RunContext
from ._common import pause

FEATURE = "checkme"
ENDPOINT = "/api/__checkme__"

#: The reference's own decision set (:1739999-1740004 region). ``STOP`` ends a
#: verified chain, ``GIVEUP`` ends a failed one, ``WAIT`` sleeps one round,
#: ``NEED_ACTION`` runs the answer's actions and resubmits, ``NEED_MANUAL``
#: hands the join to a person, ``UNKNOWN`` is an answer without the
#: ``checkme`` key - treated like WAIT so a talky service cannot wedge us.
DECISIONS = ("STOP", "GIVEUP", "WAIT", "NEED_ACTION", "NEED_MANUAL", "UNKNOWN")

#: Action kinds the answer's ``actions`` array may carry. The ones this build
#: performs are the session-shaped ones; the OPEN_* kinds need a UI the CLI
#: does not have, so they come back as a manual line (the reference's own
#: NEED_MANUAL idea, applied per action).
ACTION_KINDS = (
    "SEND_MESSAGE",
    "OPEN_BOT",
    "OPEN_URL",
    "OPEN_MINIAPP",
    "CLICK_BUTTON",
    "VOTE_POLL",
    "FETCH_LATEST_MEDIA",
    "FETCH_LATEST_MESSAGES",
)

#: The answer's full protocol vocabulary (:1739958-1739981).  The client
#: used to read only ``checkme`` / ``reason`` / ``state_token`` / ``ttl_sec`` /
#: ``need_resubmit`` / ``min_delay_ms`` / ``delay_ms`` / ``actions``; the
#: passive-observe and captcha keys below are the rest of the same block.
ANSWER_KEYS = (
    "checkme",
    "state_token",
    "ttl_sec",
    "reason",
    "actions",
    "need_resubmit",
    "min_delay_ms",
    "trigger",
    "next",
    "peer_kind",
    "can_send_text",
    "global_mute_text",
    "bot_captcha",
    "delay_ms",
    "lookback_sec",
    "auto_click_single_callback",
    "startapp_payload",
)

#: The three keys the reference's permissions snapshot prints (:1740022).
PERMISSION_KEYS = ("global_mute_text", "can_send_text", "can_send_photo")


@dataclass
class CheckmeAnswer:
    """One answer, with the protocol's keys read as fields.

    ``bot_captcha`` carries its own ``delay_ms`` / ``lookback_sec`` /
    ``auto_click_single_callback`` triple (:1739971-1739974); ``next`` and
    ``trigger`` decide whether the passive observe window is extended.
    """

    raw: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    @property
    def decision(self) -> str:
        return str(self.get("checkme", "")).upper()

    @property
    def reason(self) -> str:
        return str(self.get("reason", ""))

    @property
    def token(self) -> str:
        return str(self.get("state_token", "") or "")

    @property
    def ttl(self) -> float | None:
        value = self.get("ttl_sec")
        return float(value) if value is not None else None

    @property
    def permissions(self) -> dict[str, Any]:
        return {key: self.get(key) for key in PERMISSION_KEYS if key in self.raw}

    @property
    def captcha(self) -> dict[str, Any]:
        value = self.get("bot_captcha")
        return value if isinstance(value, dict) else {}

    @property
    def observe_remain(self) -> float:
        """The window the answer asks for, from ``next`` or the top level."""
        nxt = self.get("next")
        value = self.get("lookback_sec")
        if isinstance(nxt, dict) and nxt.get("lookback_sec") is not None:
            value = nxt.get("lookback_sec")
        try:
            return max(0.0, float(value or 0))
        except (TypeError, ValueError):
            return 0.0

    @property
    def passive(self) -> bool:
        trigger = str(self.get("trigger", "")).casefold()
        return (
            trigger in ("passive", "passivemode", "observe") or self.observe_remain > 0
        )

    def keys_seen(self) -> tuple[str, ...]:
        """Which of :data:`ANSWER_KEYS` this answer actually carried."""
        return tuple(key for key in ANSWER_KEYS if key in self.raw)


@dataclass
class CheckmeOutcome:
    """How one join's verification chain ended."""

    ok: bool
    status: str
    reason: str
    rounds: int
    actions_done: int
    trace_id: str = ""
    state_token: str = ""


class CheckmeExecutor:
    """Performs the answer's actions in the joining account's session.

    ``perform`` returns ``(done, detail)``; a ``False`` fails the round. The
    base class is the honest default: nothing is performed, every session
    action reports as manual-only. The Telegram subclass in ``batch_join``
    replaces it where a client actually exists.
    """

    def perform(self, action: dict[str, Any]) -> tuple[bool, str]:
        kind = str(action.get("action", action.get("kind", "")))
        if kind in ("OPEN_BOT", "OPEN_URL", "OPEN_MINIAPP"):
            return False, f"{kind} is manual-only in this build"
        return False, f"{kind or 'UNKNOWN'} has no executor in this build"


def _post(url: str, payload: dict[str, Any], timeout: float) -> tuple[int, str]:
    """One POST, returning ``(http status, body)``; network errors raise."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return int(answer.status), answer.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return int(error.code), error.read().decode("utf-8", "replace")


@dataclass
class CheckmeClient:
    """The round state machine, one instance per (account, join)."""

    config: CheckmeConfig
    ctx: RunContext
    account: str
    account_index: int
    peer: str
    task_id: int
    executor: CheckmeExecutor = field(default_factory=CheckmeExecutor)
    join_source: str = "batch_join"
    observe_remain: float = 0.0
    bot_evidence: int = 0

    def _observe(self, answer: CheckmeAnswer, trace_id: str) -> None:
        """The answer's passive-observe / captcha side of the protocol.

        :1740022 permissions snapshot, :1740010 the extended observe window,
        :1740061 the fetch_latest_messages start, :1740059 click_button_verify
        and :1740025 the cached bot evidence line.  None of these changes the
        decision; they are the manager's own view of the same answer.
        """
        permissions = answer.permissions
        if permissions:
            print(
                f"CheckmeGroupMgr[#{self.task_id} id={self.task_id}]: "
                f"permissions snapshot: globalMuteText="
                f"{permissions.get('global_mute_text', '-')} "
                f"canSendText={permissions.get('can_send_text', '-')} "
                f"canSendPhoto={permissions.get('can_send_photo', '-')}."
            )
        if answer.passive:
            old = self.observe_remain
            self.observe_remain = max(old, answer.observe_remain)
            print(
                f"CheckmeGroupMgr[#{self.task_id}]: extend passive observe "
                f"window identifier={trace_id} "
                f"remain={self.observe_remain:.0f}s(old={old:.0f}s)"
            )
        captcha = answer.captcha
        if captcha:
            delay_ms = captcha.get("delay_ms") or 0
            lookback = captcha.get("lookback_sec") or answer.observe_remain
            auto = bool(captcha.get("auto_click_single_callback"))
            print(
                f"CheckmeGroupMgr[#{self.task_id} id={self.task_id}]: "
                f"fetch_latest_messages start target_peer={self.peer} "
                f"identifier={trace_id} delay_ms={float(delay_ms):.0f} "
                f"lookback_sec={float(lookback):.0f} auto_click={int(auto)}."
            )
            if auto:
                print(
                    f"CheckmeGroupMgr[#{self.task_id}]: click_button_verify "
                    f"group_link={self.peer} "
                    f"bot_username={captcha.get('bot_username', '-')} "
                    f"message_id={captcha.get('message_id', '-')} "
                    f"button={captcha.get('button', '-')}"
                )
            else:
                self.bot_evidence += int(captcha.get("messages") or 0)
                print(
                    f"CheckmeGroupMgr[#{self.task_id} id={self.task_id}]: "
                    f"bot_captcha: reuse cached bot evidence "
                    f"messages={self.bot_evidence} "
                    f"bot_peer={captcha.get('bot_peer', '-')}."
                )

    def run(self) -> CheckmeOutcome:
        trace_id = uuid.uuid4().hex[:12]
        token = ""
        token_deadline = 0.0
        rounds = 0
        actions_done = 0
        start_payload = ""
        while True:
            rounds += 1
            # The two local give-up lines run before the round is spent
            # (:1740027-1740029): the round cap, then the token's own ttl.
            if rounds > max(1, self.config.max_round):
                print(
                    f"CheckmeGroupMgr[#{self.task_id}]: "
                    f"maxRound reached ({self.config.max_round}), give up."
                )
                return self._finish(
                    "GIVEUP",
                    "maxRound reached",
                    rounds,
                    actions_done,
                    trace_id,
                    token,
                )
            if token and time.monotonic() > token_deadline:
                print(
                    f"CheckmeGroupMgr[#{self.task_id}]: state_token expired, give up."
                )
                return self._finish(
                    "GIVEUP",
                    "state_token expired",
                    rounds,
                    actions_done,
                    trace_id,
                    token,
                )
            payload = {
                "account": self.account,
                "account_index": self.account_index,
                "stage": "join",
                "round": rounds,
                "trace_id": trace_id,
                "peer_id": self.peer,
            }
            if start_payload:
                # :1739947: the OPEN_BOT answer hands back a startapp
                # payload, which the next submit carries as start_payload.
                payload["start_payload"] = start_payload
            print(
                f"CheckmeGroupMgr[#{self.task_id} id={self.task_id} "
                f"peer={self.peer} stage=join round={rounds} "
                f"trace_id={trace_id}]: submit payload_json="
                f"{json.dumps(payload, ensure_ascii=False)}"
            )
            url = self.config.base_url.strip().rstrip("/") + ENDPOINT
            try:
                status, body = _post(url, payload, timeout=30.0)
            except Exception as error:  # noqa: BLE001 - the failed line maps it
                print(
                    f"CheckmeGroupMgr[#{self.task_id} id={self.task_id}]: "
                    f"__checkme__ failed http=0 error={error} "
                    f"peer={self.peer} stage=join round={rounds} "
                    f"state_token={token or '-'} trace_id={trace_id}"
                )
                pause(self.ctx, self.config.delay_sec)
                continue
            if status != 200:
                print(
                    f"CheckmeGroupMgr[#{self.task_id} id={self.task_id}]: "
                    f"__checkme__ failed http={status} error=response body "
                    f"peer={self.peer} stage=join round={rounds} "
                    f"state_token={token or '-'} trace_id={trace_id}"
                )
                pause(self.ctx, self.config.delay_sec)
                continue
            answer = CheckmeAnswer(_parse(body))
            decision = answer.decision
            reason = answer.reason
            new_token = answer.token or token
            ttl = answer.ttl
            if new_token:
                token = new_token
                if ttl is not None:
                    token_deadline = time.monotonic() + max(0.0, ttl)
            print(
                f"CheckmeGroupMgr[#{self.task_id} id={self.task_id}]: "
                f"__checkme__ status={decision or 'UNKNOWN'} reason={reason or '-'} "
                f"state_token={token or '-'} peer={self.peer} stage=join "
                f"round={rounds} trace_id={trace_id}"
            )
            self._observe(answer, trace_id)
            nxt = answer.get("next")
            if isinstance(nxt, dict) and nxt.get("startapp_payload"):
                start_payload = str(nxt["startapp_payload"])
            elif answer.get("startapp_payload"):
                start_payload = str(answer.get("startapp_payload"))
            if decision == "STOP":
                return self._finish(
                    "VERIFIED",
                    reason or "verified",
                    rounds,
                    actions_done,
                    trace_id,
                    token,
                )
            if decision == "NEED_MANUAL":
                return self._finish(
                    "NEED_MANUAL",
                    reason or "manual",
                    rounds,
                    actions_done,
                    trace_id,
                    token,
                )
            if decision == "GIVEUP":
                return self._finish(
                    "GIVEUP",
                    reason or "service said giveup",
                    rounds,
                    actions_done,
                    trace_id,
                    token,
                )
            if decision == "NEED_ACTION":
                actions = answer.get("actions")
                if not isinstance(actions, list):
                    actions = []
                failed = False
                for item in actions:
                    if not isinstance(item, dict):
                        continue
                    done, detail = self.executor.perform(item)
                    if done:
                        actions_done += 1
                        continue
                    print(
                        f"CheckmeGroupMgr[#{self.task_id} id={self.task_id}]: "
                        f"__checkme__ action failed: {detail}"
                    )
                    failed = True
                    break
                if failed:
                    return self._finish(
                        "FAILED",
                        "action failed",
                        rounds,
                        actions_done,
                        trace_id,
                        token,
                    )
                # ``need_resubmit`` or a performed action re-submits at once;
                # ``min_delay_ms`` is the floor the service asks for.
                if actions_done or answer.get("need_resubmit"):
                    floor = float(answer.get("min_delay_ms") or 0) / 1000.0
                    if floor:
                        pause(self.ctx, floor)
                    continue
                pause(self.ctx, self.config.delay_sec)
                continue
            # WAIT, UNKNOWN, or anything unrecognized: one round's sleep.
            delay_ms = answer.get("delay_ms")
            wait = (
                float(delay_ms) / 1000.0
                if delay_ms is not None
                else self.config.delay_sec
            )
            pause(self.ctx, max(0.0, wait))

    def _finish(
        self,
        status: str,
        reason: str,
        rounds: int,
        actions_done: int,
        trace_id: str,
        token: str,
    ) -> CheckmeOutcome:
        print(
            f"CheckmeGroupMgr[#{self.task_id} id={self.task_id}]: finish "
            f"status={status} reason={reason} peer={self.peer} "
            f"join_source={self.join_source} task_id={self.task_id} "
            f"stage=join round={rounds} state_token={token or '-'} "
            f"trace_id={trace_id}."
        )
        return CheckmeOutcome(
            ok=status == "VERIFIED",
            status=status,
            reason=reason,
            rounds=rounds,
            actions_done=actions_done,
            trace_id=trace_id,
            state_token=token,
        )


def _parse(body: str) -> dict[str, Any]:
    try:
        answer = json.loads(body)
    except ValueError:
        return {}
    return answer if isinstance(answer, dict) else {}


def verify_join(
    config: CheckmeConfig,
    ctx: RunContext,
    account: str,
    account_index: int,
    peer: str,
    task_id: int,
    executor: CheckmeExecutor | None = None,
) -> CheckmeOutcome:
    """One join's verification chain; the batch_join call site."""
    client = CheckmeClient(
        config=config,
        ctx=ctx,
        account=account,
        account_index=account_index,
        peer=peer,
        task_id=task_id,
        executor=executor or CheckmeExecutor(),
    )
    return client.run()


def actions_of(answer: dict[str, Any]) -> Sequence[dict[str, Any]]:
    """The answer's actions array, safe when the service sends junk."""
    items = answer.get("actions")
    if not isinstance(items, list):
        return ()
    return tuple(item for item in items if isinstance(item, dict))


class TelegramExecutor(CheckmeExecutor):
    """Performs session-shaped actions in the joining account's client.

    ``SEND_MESSAGE`` posts the answer's text into the joined chat,
    ``CLICK_BUTTON`` presses the callback/data button the answer names
    (``data_b64``), ``VOTE_POLL`` picks ``option_index`` on the poll, and the
    two FETCH kinds read the chat back so the next round has evidence. The
    OPEN_* kinds are returned as manual-only: this build has no browser and
    no mini-app front end, which is the reference's own NEED_MANUAL boundary.
    """

    def __init__(self, sender: Any, account: str, peer: str) -> None:
        self.sender = sender
        self.account = account
        self.peer = peer

    def perform(self, action: dict[str, Any]) -> tuple[bool, str]:
        kind = str(action.get("action", action.get("kind", "")))
        try:
            if kind == "SEND_MESSAGE":
                return self._send_message(action)
            if kind == "CLICK_BUTTON":
                return self._click_button(action)
            if kind == "VOTE_POLL":
                return self._vote_poll(action)
            if kind in ("FETCH_LATEST_MESSAGES", "FETCH_LATEST_MEDIA"):
                return self._fetch_latest(action)
        except Exception as error:  # noqa: BLE001 - one action, one failure
            return False, f"{kind} failed: {error}"
        return super().perform(action)

    def _send_message(self, action: dict[str, Any]) -> tuple[bool, str]:
        text = str(action.get("text", ""))
        if not text:
            return False, "SEND_MESSAGE without text"
        client = self.sender.client_for(self.account)
        client.send_message(self.peer, text)
        return True, f"SEND_MESSAGE sent ({len(text)} chars)"

    def _click_button(self, action: dict[str, Any]) -> tuple[bool, str]:
        message_id = int(action.get("message_id") or 0)
        raw_data = str(action.get("data_b64", ""))
        data = base64.b64decode(raw_data) if raw_data else None
        client = self.sender.client_for(self.account)
        message = client.get_messages(self.peer, ids=message_id)
        if message is None:
            return False, f"CLICK_BUTTON: message {message_id} not found"
        message.click(data=data)
        return True, f"CLICK_BUTTON clicked message {message_id}"

    def _vote_poll(self, action: dict[str, Any]) -> tuple[bool, str]:
        message_id = int(action.get("message_id") or 0)
        option = int(action.get("option_index") or 0)
        client = self.sender.client_for(self.account)
        message = client.get_messages(self.peer, ids=message_id)
        if message is None:
            return False, f"VOTE_POLL: message {message_id} not found"
        message.click(option)
        return True, f"VOTE_POLL option {option} on message {message_id}"

    def _fetch_latest(self, action: dict[str, Any]) -> tuple[bool, str]:
        limit = int(action.get("limit") or 10)
        client = self.sender.client_for(self.account)
        messages = client.get_messages(self.peer, limit=max(1, limit))
        return True, f"{len(list(messages))} message(s) read back as evidence"
