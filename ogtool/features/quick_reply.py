"""#T5 QuickReply - the right toolbar's shortcut message panel.

The page keeps two lists: categories (``CategoryListWidget`` /
``New Category``, :1741732-1734) and the quick messages inside one
(``MessageListWidget`` / ``New quick message``), a slot being ``/[1-8]``
(:1741728).  Sending one into a chat prints the page's own pair -
"Message sent successfully!" with the four fact lines (:1741736-1741740)
or "Failed to send message" / "Failed to send to %1" (:1741742-1741743) -
and the two selection refusals come before any send (:1741745-1741746).
``Sending quick reply to`` (:1741748) opens the attempt; the slot grammar
is the page's own ``/[1-8]``.

The reference binds this to a picked window ("send message to current
chat id", :1741744); the CLI takes the target explicitly, and a dry run
plans instead of sending.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import Settings
from ..flags import Flag
from ..runtime import RunContext
from ..senders import Content
from ..store import Store
from ..targets import parse_many
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    name_of,
    open_task,
    record,
    section,
    text,
)

FEATURE = "quick_reply"
SECTION = "quick_reply"
TITLE = "#T5 快捷回复"

FLAGS = (
    Flag("--add-category", "add_category", str, "New Category"),
    Flag(
        "--add-message",
        "add_message",
        str,
        "category|shortcut|text (New quick message)",
        action="append",
    ),
    Flag(
        "--delete-message",
        "delete_message",
        str,
        "category|shortcut (repeatable)",
        action="append",
    ),
    Flag("--category", "category", str, "one category to list"),
    Flag("--list", "list_only", None, "list the stored slots", action="store_true"),
    Flag("--send", "send", str, "category|shortcut to send"),
    Flag("--target", "target", str, "chat the quick message goes to"),
)

#: The page's own slot grammar (:1741728): one digit, one through eight.
SLOT_PATTERN = re.compile(r"^/([1-8])$")


def slot_error(slot: str) -> str | None:
    """The slot grammar's verdict, or ``None`` when the slot is valid."""
    if not SLOT_PATTERN.match(slot.strip()):
        return f"Invalid quick message slot: {slot}"
    return None


@dataclass(slots=True)
class QuickReplyConfig:
    account_labels: list[str] = field(default_factory=list)
    add_category: str = ""
    add_message: list[str] = field(default_factory=list)
    delete_message: list[str] = field(default_factory=list)
    category: str = ""
    list_only: bool = False
    send: str = ""
    target: str = ""
    task_name: str = "quick-reply"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> QuickReplyConfig:
        data = section(raw, SECTION)
        return cls(
            account_labels=accounts_of(raw, data),
            add_category=text(data, "add_category"),
            add_message=[str(item) for item in (data.get("add_message") or [])],
            delete_message=[str(item) for item in (data.get("delete_message") or [])],
            category=text(data, "category"),
            list_only=bool(data.get("list_only", False)),
            send=text(data, "send"),
            target=text(data, "target"),
            task_name=name_of(raw, data, "quick-reply"),
        )


def _parse_pair(spec: str) -> tuple[str, str]:
    left, _, right = spec.partition("|")
    return left.strip(), right.strip()


def run(config: QuickReplyConfig, ctx: RunContext) -> int:
    store = Store(ctx.settings.db_path)
    header(
        TITLE,
        accounts=f"{len(config.account_labels)}"
        + (" (no account bound, plan only)" if not config.account_labels else ""),
        category=config.category or "-",
        send=config.send or "-",
        target=config.target or "-",
    )
    task = open_task(
        ctx,
        config.task_name,
        FEATURE,
        {"send": config.send, "target": config.target},
        planned=1 if config.send else 0,
    )
    failed = 0
    if config.add_category:
        print(f"New Category: {config.add_category}")
    for spec in config.add_message:
        category, rest = _parse_pair(spec)
        slot, _, message = rest.partition("|")
        slot = slot.strip()
        error = slot_error(slot)
        if error:
            print(error)
            failed += 1
            continue
        store.upsert_quick_reply(category, slot, message.strip())
        print(f"New quick message: {category} {slot}")
    for spec in config.delete_message:
        category, slot = _parse_pair(spec)
        if store.delete_quick_reply(category, slot):
            print(f"Deleted: {category} {slot}")
        else:
            print(f"Failed to delete: {category} {slot}")
            failed += 1
    if config.send:
        failed += _send(config, store, ctx, task)
    elif config.list_only or not (
        config.add_category or config.add_message or config.delete_message
    ):
        for row in store.quick_reply_rows(config.category):
            print(
                f"{row['category']} {row['shortcut']} "
                f'"{row["text"]}" server={row["server_id"] or "-"}'
            )
        rows = store.quick_reply_rows(config.category)
        print(f"quick reply: {len(rows)} slot(s)")
    close_task(ctx, task, ok=1 if config.send and not failed else 0, failed=failed)
    return 0 if not failed else 1


def _send(config: QuickReplyConfig, store: Store, ctx: RunContext, task) -> int:
    """One slot into one chat, with the page's own verdict lines."""
    category, slot = _parse_pair(config.send)
    if slot_error(slot):
        print(f"Invalid quick message slot: {slot}")
        return 1
    if not config.target:
        print("Please select a chat first")
        return 1
    row = store.quick_reply_get(category, slot)
    if row is None or not row["text"]:
        print("Please select a window first")
        return 1
    target = parse_many([config.target], "custom")[0]
    print(f"Sending quick reply to {target.display()}")
    if not ctx.live:
        print("dry-run: no message was sent")
        return 0
    account = config.account_labels[0] if config.account_labels else "-"
    sender = ctx.sender()
    outcome = sender.send(account, target, Content(text=row["text"]))
    if outcome.ok:
        print(" Message sent successfully!")
        print(f"   Server ID: {outcome.message_id or '-'}")
        print(f"   To: {account}")
        print(f"   Peer: {target.display()}")
        print(f"   Text: {row['text'][:60]}")
        print(f" Sent to {target.display()} (ID: {outcome.message_id or '-'})")
        store.upsert_quick_reply(
            category, slot, row["text"], str(outcome.message_id or "")
        )
        record(ctx, task, account, target.display(), True, detail=slot)
        return 0
    print(" Failed to send message")
    print(f" Failed to send to {target.display()}")
    record(ctx, task, account, target.display(), False, detail=outcome.code)
    return 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(QuickReplyConfig.from_dict(raw, ctx.settings), ctx)


__all__ = [
    "FEATURE",
    "QuickReplyConfig",
    "SLOT_PATTERN",
    "run",
    "slot_error",
]
