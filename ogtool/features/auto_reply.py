"""#6 auto reply: keyword rules with a duplicate cache window.

The page carries two things: the rules (keyword -> reply) and the duplicate
cache window in seconds, in which the same text from the same chat is not
answered twice.  Both are honoured here, and a dry run replays a supplied
set of incoming messages so the decisions can be read without a session.

The rules page itself (:1741027-1741048, ``tdata/auto_reply_config.json``)
is a CRUD surface: ``Keyword / Reply Content / Add Rule``, the no-keyword and
no-content refusals, the first-time default rule, add/delete/batch-delete/
update/enable lines and the duplicate-interval setter.  ``RuleStore`` is
that surface; the CLI reaches it through the page's own flags.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..flags import Flag
from ..limiter import format_duration
from ..runtime import RunContext
from . import register
from ._common import close_task, open_task, record

FEATURE = "auto_reply"
DEFAULT_RULES_FILE = "tdata/auto_reply_config.json"

FLAGS = (
    Flag("--rule-add", "rule_add", str, "keyword=>reply (repeatable)", action="append"),
    Flag(
        "--rule-delete", "rule_delete", str, "rule name (repeatable)", action="append"
    ),
    Flag("--rule-update", "rule_update", str, "name=>keyword=>reply", action="append"),
    Flag("--rule-state", "rule_state", str, "name=>enabled|disabled", action="append"),
    Flag(
        "--duplicate-interval",
        "duplicate_interval",
        int,
        "Set auto reply duplicate interval to N seconds",
    ),
    Flag("--rules-file", "rules_file", str, "the page's tdata/auto_reply_config.json"),
    Flag(
        "--rules-list",
        "rules_list",
        None,
        "list the stored rules instead of listening",
        action="store_true",
    ),
)


class RuleStore:
    """The page's ``auto_reply_config.json`` and its own log sentences."""

    def __init__(self, path: str | Path = DEFAULT_RULES_FILE) -> None:
        self.path = Path(path).expanduser()
        self.data: dict = {}

    def load(self) -> dict:
        if not self.path.is_file():
            print("Initialized default auto-reply rule for first-time user")
            self.data = {
                "enabled": True,
                "duplicate_seconds": 60,
                "rules": [],
            }
            return self.data
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            parsed = {}
        self.data = parsed if isinstance(parsed, dict) else {}
        self.data.setdefault("enabled", True)
        self.data.setdefault("duplicate_seconds", 60)
        if not isinstance(self.data.get("rules"), list):
            self.data["rules"] = []
        return self.data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _rules(self) -> list[dict]:
        return [item for item in self.data["rules"] if isinstance(item, dict)]

    def add(self, spec: str) -> bool:
        keyword, _, reply = spec.partition("=>")
        keyword, reply = keyword.strip(), reply.strip()
        if not keyword:
            print("No keyword")
            return False
        if not reply:
            print("No content")
            return False
        rules = self._rules()
        name = f"rule{len(rules) + 1}"
        rules.append(
            {
                "name": name,
                "keyword": keyword,
                "replyContent": reply,
                "enabled": True,
            }
        )
        self.data["rules"] = rules
        print(f"Added new rule: {name}")
        return True

    def delete(self, names: list[str]) -> bool:
        if not names:
            print("No rules selected for deletion")
            return False
        rules = self._rules()
        kept = [item for item in rules if str(item.get("name", "")) not in names]
        removed = len(rules) - len(kept)
        if removed == 1 and len(names) == 1:
            print(f"Deleted rule: {names[0]}")
        elif removed:
            print(f"Batch deleted {removed} rules")
        else:
            print("No rules selected for deletion")
            return False
        self.data["rules"] = kept
        return True

    def update(self, spec: str) -> bool:
        parts = spec.split("=>")
        if len(parts) < 3:
            print("No keyword")
            return False
        name, keyword, reply = (part.strip() for part in parts[:3])
        if not keyword:
            print("No keyword")
            return False
        if not reply:
            print("No content")
            return False
        for item in self._rules():
            if str(item.get("name", "")) == name:
                item["keyword"] = keyword
                item["replyContent"] = reply
                print(f"Updated rule: {name}")
                return True
        print("No rules selected for deletion")
        return False

    def state(self, spec: str) -> bool:
        name, _, value = spec.partition("=>")
        name, value = name.strip(), value.strip().casefold()
        for item in self._rules():
            if str(item.get("name", "")) == name:
                item["enabled"] = value in ("enabled", "true", "1", "on")
                print(f"Rule {name} {value or 'disabled'}")
                return True
        print("No rules selected for deletion")
        return False

    def interval(self, seconds: int) -> None:
        self.data["duplicate_seconds"] = max(0, int(seconds))
        print(f"Set auto reply duplicate interval to {seconds} seconds")

    def listing(self) -> None:
        print(f"Auto reply {self.data.get('enabled', True)}")
        print(f"Duplicate Cache(Seconds): {self.data.get('duplicate_seconds', 60)}")
        for item in self._rules():
            print(
                f"{item.get('name', '-')} keyword={item.get('keyword', '-')} "
                f"replyContent={item.get('replyContent', '-')} "
                f"enabled={item.get('enabled', True)}"
            )


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
    #: The rules page's CRUD switches.  A run carrying any of these edits
    #: the ``auto_reply_config.json`` and returns instead of listening.
    rule_add: list[str] = field(default_factory=list)
    rule_delete: list[str] = field(default_factory=list)
    rule_update: list[str] = field(default_factory=list)
    rule_state: list[str] = field(default_factory=list)
    duplicate_interval: int | None = None
    rules_file: str = DEFAULT_RULES_FILE
    rules_list: bool = False

    @property
    def editing(self) -> bool:
        return bool(
            self.rule_add
            or self.rule_delete
            or self.rule_update
            or self.rule_state
            or self.duplicate_interval is not None
            or self.rules_list
        )

    @classmethod
    def from_dict(cls, raw: dict) -> AutoReplyConfig:
        section = raw.get("auto_reply") or {}
        rules: list[Rule] = []
        for index, item in enumerate(section.get("rules") or [], start=1):
            if isinstance(item, str):
                keyword, _, reply = item.partition("=>")
                rules.append(Rule([keyword.strip()], reply.strip(), f"rule{index}"))
                continue
            keywords = item.get("keywords")
            if not keywords and item.get("keyword"):
                keywords = [item["keyword"]]
            rules.append(
                Rule(
                    [str(word) for word in (keywords or [])],
                    str(item.get("reply", "")),
                    str(item.get("name") or f"rule{index}"),
                )
            )
        return cls(
            account_labels=list(raw.get("accounts") or []),
            enabled=bool(section.get("enabled", True)),
            duplicate_seconds=int(section.get("duplicate_seconds", 60)),
            run_seconds=int(section.get("run_seconds", 0)),
            rules=rules,
            simulate=list(raw.get("simulate") or []),
            task_name=str(raw.get("task_name") or "auto-reply"),
            rule_add=list(section.get("rule_add") or []),
            rule_delete=list(section.get("rule_delete") or []),
            rule_update=list(section.get("rule_update") or []),
            rule_state=list(section.get("rule_state") or []),
            duplicate_interval=(
                int(section["duplicate_interval"])
                if section.get("duplicate_interval") is not None
                else None
            ),
            rules_file=str(section.get("rules_file") or DEFAULT_RULES_FILE),
            rules_list=bool(section.get("rules_list", False)),
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
        messages.append(Incoming(parts[0].strip(), parts[1].strip(), parts[2].strip()))
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
            decisions.append(
                (message, None, f"duplicate within {config.duplicate_seconds}s")
            )
            continue
        rule = next((item for item in config.rules if item.matches(message.text)), None)
        decisions.append((message, rule, "matched" if rule else "no rule matched"))
    return decisions


def _edit_rules(config: AutoReplyConfig) -> int:
    """The rules page's CRUD run; no session is opened for an edit."""
    store = RuleStore(config.rules_file)
    store.load()
    failed = succeeded = 0
    for spec in config.rule_add:
        if store.add(spec):
            succeeded += 1
        else:
            failed += 1
    if config.rule_delete:
        if store.delete(config.rule_delete):
            succeeded += 1
        else:
            failed += 1
    for spec in config.rule_update:
        if store.update(spec):
            succeeded += 1
        else:
            failed += 1
    for spec in config.rule_state:
        if store.state(spec):
            succeeded += 1
        else:
            failed += 1
    if config.duplicate_interval is not None:
        store.interval(config.duplicate_interval)
        succeeded += 1
    editing = bool(
        config.rule_add
        or config.rule_delete
        or config.rule_update
        or config.rule_state
        or config.duplicate_interval is not None
    )
    if config.rules_list or not editing:
        store.listing()
    elif succeeded:
        store.save()
        print(f"Auto reply {store.data.get('enabled', True)}")
    return 1 if failed else 0


def run(config: AutoReplyConfig, ctx: RunContext) -> int:
    if config.editing:
        return _edit_rules(config)
    accounts = ctx.bind(config.account_labels or None)
    messages = parse_simulated(config.simulate)
    start = datetime.now(UTC).replace(microsecond=0)

    print(f"task: {config.task_name}")
    print(
        f"accounts: {len(accounts)}  rules: {len(config.rules)}  "
        f"duplicate cache: {config.duplicate_seconds}s  "
        f"enabled: {config.enabled}"
    )

    decisions = decide(config, messages, start)
    replies = 0
    for message, rule, reason in decisions:
        print(f'IN  peer={message.peer} sender={message.sender} text="{message.text}"')
        if rule is not None:
            replies += 1
            print(f'✅ OUT -> {rule.name} reply="{rule.reply}"')
        else:
            print(f"SKIP: {reason}")

    print(
        f"PLAN: {replies} replies, {len(accounts)} accounts, ETA {format_duration(0)}"
    )

    task = open_task(
        ctx,
        config.task_name,
        FEATURE,
        {
            "rules": [rule.name for rule in config.rules],
            "keywords": [rule.keywords for rule in config.rules],
            "duplicate_seconds": config.duplicate_seconds,
            "enabled": config.enabled,
        },
        replies,
    )

    if not ctx.live:
        # One row per reply this run would have sent: "PLAN: 1 replies" with an
        # empty table is a claim nobody can check afterwards.
        planned = 0
        for message, rule, _reason in decisions:
            if rule is None:
                continue
            record(
                ctx,
                task,
                "auto",
                str(message.peer),
                ok=True,
                detail=f'rule={rule.name} text="{message.text}"',
            )
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
        rule = next(
            (item for item in config.rules if item.matches(event.raw_text)), None
        )
        if rule is None:
            return
        await event.reply(rule.reply)
        answered += 1
        record(ctx, task, "auto", str(event.chat_id), True, detail=rule.name)

    for client in clients:  # pragma: no cover - live only
        client.add_event_handler(handler)
    stop_note = (
        f"stopping after {config.run_seconds}s"
        if config.run_seconds
        else "Ctrl+C to stop"
    )
    print(f"live: watching {len(clients)} account(s), {stop_note}")
    try:  # pragma: no cover - live only
        import asyncio

        loop = asyncio.get_event_loop()
        if config.run_seconds:
            # A self-limiting listen: the receipt closes through the same
            # finally as an interrupt, but the exit is the run's own plan,
            # not a signal someone had to send.
            loop.call_later(
                config.run_seconds, lambda: loop.call_soon_threadsafe(loop.stop)
            )
            loop.run_forever()
        else:
            loop.run_forever()
    except KeyboardInterrupt:
        ctx.note("interrupted by the operator", "warn")
    finally:
        # The receipt is written here, not by the CLI's fallback: a run receipt
        # has no rules, no planned count and no accounts, which is everything
        # this page is about. ``plan_rows``: the plan was "whatever arrives",
        # so the task's planned count is back-filled to the replies it sent --
        # planned 0 with one reply row failed the receipts invariant.
        close_task(ctx, task, ok=answered, failed=0, plan_rows=True)
    print(f"DONE: replies={answered}")
    return 0


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(AutoReplyConfig.from_dict(raw), ctx)
