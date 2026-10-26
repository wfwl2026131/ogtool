"""#6 auto reply: keyword rules with a duplicate cache window.

The page carries two things: the rules (keyword -> reply) and the duplicate
cache window in seconds, in which the same text from the same chat is not
answered twice.  Both
are honoured here, and a dry run replays a supplied set of incoming messages so
the decisions can be read without a session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ..limiter import format_duration
from ..runtime import RunContext
from . import register
from ._common import close_task, open_task, record

FEATURE = "auto_reply"


@dataclass(slots=True)
class Rule:
    keywords: list[str]
    reply: str
    name: str = ""

    def matches(self, text: str) -> str:
        """Return the keyword that matched, or an empty string."""
        lowered = text.casefold()
        for keyword in self.keywords:
            if keyword and keyword.casefold() in lowered:
                return keyword
        return ""


@dataclass(slots=True)
class AutoReplyConfig:
    account_labels: list[str] = field(default_factory=list)
    enabled: bool = True
    duplicate_seconds: int = 60
    rules: list[Rule] = field(default_factory=list)
    simulate: list[str] = field(default_factory=list)
    task_name: str = "auto-reply"
    #: Stop listening after this many seconds and close the receipt. 0 means
    #: until interrupted - the operator's Ctrl+C is still the default.
    run_seconds: int = 0

    @classmethod
    def from_dict(cls, raw: dict) -> AutoReplyConfig:
        section = raw.get("auto_reply") or {}
        rules: list[Rule] = []
        for index, item in enumerate(section.get("rules") or [], start=1):
            if isinstance(item, str):
                keyword, _, reply = item.partition("=>")
                rules.append(Rule([keyword.strip()], reply.strip(),
                                  f"rule{index}"))
                continue
            keywords = item.get("keywords")
            if not keywords and item.get("keyword"):
                keywords = [item["keyword"]]
            rules.append(Rule(
                [str(word) for word in (keywords or [])],
                str(item.get("reply", "")),
                str(item.get("name") or f"rule{index}"),
            ))
        return cls(
            account_labels=list(raw.get("accounts") or []),
            enabled=bool(section.get("enabled", True)),
            duplicate_seconds=int(section.get("duplicate_seconds", 60)),
            run_seconds=int(section.get("run_seconds", 0)),
            rules=rules,
            simulate=list(raw.get("simulate") or []),
            task_name=str(raw.get("task_name") or "auto-reply"),
        )


@dataclass(slots=True)
class Incoming:
    peer: str
    sender: str
    text: str


def parse_simulated(entries: list[str]) -> list[Incoming]:
    """``peer|sender|text`` lines, so one dry run covers several chats."""
    messages: list[Incoming] = []
    for entry in entries:
        parts = entry.split("|", 2)
        if len(parts) != 3:
            parts = ["@demo", "tester", entry]
        messages.append(Incoming(parts[0].strip(), parts[1].strip(),
                                 parts[2].strip()))
    return messages


class DuplicateCache:
    """The duplicate window: same chat plus same text, inside N seconds."""

    def __init__(self, seconds: int) -> None:
        self.seconds = max(0, int(seconds))
        self._seen: dict[tuple[str, str], datetime] = {}

    def seen(self, peer: str, text: str, now: datetime) -> bool:
        if self.seconds <= 0:
            return False
        key = (peer, text)
        previous = self._seen.get(key)
        self._seen[key] = now
        if previous is None:
            return False
        return (now - previous) <= timedelta(seconds=self.seconds)


def decide(
    config: AutoReplyConfig,
    messages: list[Incoming],
    start: datetime,
) -> list[tuple[Incoming, Rule | None, str]]:
    """Return (message, matched rule or None, reason) for every incoming line."""
    cache = DuplicateCache(config.duplicate_seconds)
    decisions: list[tuple[Incoming, Rule | None, str]] = []
    for offset, message in enumerate(messages):
        moment = start + timedelta(seconds=offset)
        if not config.enabled:
            decisions.append((message, None, "disabled"))
            continue
        if cache.seen(message.peer, message.text, moment):
            decisions.append((message, None,
                              f"duplicate within {config.duplicate_seconds}s"))
            continue
        rule = next((item for item in config.rules if item.matches(message.text)),
                    None)
        decisions.append((message, rule,
                          "matched" if rule else "no rule matched"))
    return decisions


def run(config: AutoReplyConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels or None)
    messages = parse_simulated(config.simulate)
    start = datetime.now(UTC).replace(microsecond=0)

    print(f"task: {config.task_name}")
    print(f"accounts: {len(accounts)}  rules: {len(config.rules)}  "
          f"duplicate cache: {config.duplicate_seconds}s  "
          f"enabled: {config.enabled}")

    decisions = decide(config, messages, start)
    replies = 0
    for message, rule, reason in decisions:
        print(f'IN  peer={message.peer} sender={message.sender} '
              f'text="{message.text}"')
        if rule is not None:
            replies += 1
            print(f"OUT -> {rule.name} reply=\"{rule.reply}\"")
        else:
            print(f"SKIP: {reason}")

    print(f"PLAN: {replies} replies, {len(accounts)} accounts, "
          f"ETA {format_duration(0)}")

    task = open_task(ctx, config.task_name, FEATURE,
                     {"rules": [rule.name for rule in config.rules],
                      "keywords": [rule.keywords for rule in config.rules],
                      "duplicate_seconds": config.duplicate_seconds,
                      "enabled": config.enabled}, replies)

    if not ctx.live:
        # One row per reply this run would have sent: "PLAN: 1 replies" with an
        # empty table is a claim nobody can check afterwards.
        planned = 0
        for message, rule, _reason in decisions:
            if rule is None:
                continue
            record(ctx, task, "auto", str(message.peer), ok=True,
                   detail=f'rule={rule.name} text="{message.text}"')
            planned += 1
        print("dry-run: no session was opened")
        close_task(ctx, task, ok=planned, failed=0, status="dry-run")
        return 0

    return _run_live(config, ctx, task)


def _run_live(config: AutoReplyConfig, ctx: RunContext, task) -> int:
    """Attach a handler to every account and answer until interrupted."""
    try:
        from telethon import events
    except ImportError:  # pragma: no cover - live only
        print("ERROR: telethon is not installed")
        return 2

    sender = ctx.sender()
    cache = DuplicateCache(config.duplicate_seconds)
    answered = 0
    clients = []
    for account in ctx.bind(config.account_labels or None):
        try:
            clients.append(sender._client(account.label))  # noqa: SLF001
        except Exception as error:  # noqa: BLE001 - reported and skipped
            ctx.note(f"{account.label}: {error}", "warn")

    @events.register(events.NewMessage(incoming=True))
    async def handler(event):  # noqa: ANN001, ANN202 - Telethon callback
        nonlocal answered
        if cache.seen(str(event.chat_id), event.raw_text, datetime.now(UTC)):
            return
        rule = next((item for item in config.rules
                     if item.matches(event.raw_text)), None)
        if rule is None:
            return
        await event.reply(rule.reply)
        answered += 1
        record(ctx, task, "auto", str(event.chat_id),
               True, detail=rule.name)

    for client in clients:  # pragma: no cover - live only
        client.add_event_handler(handler)
    stop_note = (f"stopping after {config.run_seconds}s"
                 if config.run_seconds else "Ctrl+C to stop")
    print(f"live: watching {len(clients)} account(s), {stop_note}")
    try:  # pragma: no cover - live only
        import asyncio

        loop = asyncio.get_event_loop()
        if config.run_seconds:
            # A self-limiting listen: the receipt closes through the same
            # finally as an interrupt, but the exit is the run's own plan,
            # not a signal someone had to send.
            loop.call_later(config.run_seconds,
                            lambda: loop.call_soon_threadsafe(loop.stop))
            loop.run_forever()
        else:
            loop.run_forever()
    except KeyboardInterrupt:
        ctx.note("interrupted by the operator", "warn")
    finally:
        # The receipt is written here, not by the CLI's fallback: a run receipt
        # has no rules, no planned count and no accounts, which is everything
        # this page is about.
        close_task(ctx, task, ok=answered, failed=0)
    print(f"DONE: replies={answered}")
    return 0


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(AutoReplyConfig.from_dict(raw), ctx)
