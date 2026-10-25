"""The todo list: the reference's customer-service inbox, not a to-do app.

The page is fully described in the dump even though it is not one of the 25
console entries:

    from_todo_list / from_todo_topic / from_todo_archive   (1723584-1723820)
    Deep::FromTodoManager::TodoRecord / TodoEvent / Status  (1744739-1744741)
    "insert todo list record to db failed fromId: ... receiverId: ... peerId:"

Its menu block (1744760-1744790) names every knob:

    Unread management group        a forum-enabled group, topics on, account #2
                                   needs topic admin rights
    Auto clean previous message menus   only the latest message keeps its menu
    Links per menu                 how many promo links hang under each message
    Filter service messages        hide join/leave and other service events
    Filter bot messages            ignore bot replies, keep real customer chats
    Mark Selected / Mark All / Unread / Read / Blacklisted / Forward to
    "%1 seconds ago" / "Unknown time" / "(empty message)" / "Unknown peer"

So a row is an *incoming message that became work*, keyed by
``(from_id, peer_id, receiver_id)`` - the unique index the dump declares. That
key is why the same customer writing twice bumps ``unread_count`` instead of
adding a second row: the page is a queue, not a log.

The status numbers are the dump's own: ``1 pending / 2 done / 3 archived``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: ``status`` values, straight from the dump's column comment.
PENDING, DONE, ARCHIVED = 1, 2, 3
STATUS_NAMES = {PENDING: "pending", DONE: "done", ARCHIVED: "archived"}

#: The page's placeholders, kept verbatim: they are what the list shows when a
#: field is empty, and a rebuild that prints "" instead is quietly different.
EMPTY_MESSAGE = "(empty message)"
UNKNOWN_PEER = "Unknown peer"
UNKNOWN_TIME = "Unknown time"


def ago(seconds: float) -> str:
    """The page's own relative time: ``%1 seconds|minutes|hours|days ago``."""
    if seconds is None or seconds < 0:
        return UNKNOWN_TIME
    if seconds < 60:
        return f"{int(seconds)} seconds ago"
    if seconds < 3600:
        return f"{int(seconds // 60)} minutes ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hours ago"
    return f"{int(seconds // 86400)} days ago"


@dataclass(slots=True)
class TodoRecord:
    """One queue row. The field names are the dump's column names."""

    from_id: str
    peer_id: str
    receiver_id: str = "0"
    from_name: str = ""
    peer_name: str = ""
    chat_type: int = 1
    msg_type: int = 1
    msg_id: int = 0
    title: str = ""
    msg: str = ""
    media: str = ""
    priority: int = 1
    status: int = PENDING
    is_blacklist: bool = False
    is_service: bool = False
    is_bot: bool = False
    unread_count: int = 1
    from_count: int = 1
    last_msg_at: int = 0
    created_at: int = 0
    updated_at: int = 0
    id: int = 0

    def sender_label(self) -> str:
        return self.from_name or self.from_id or UNKNOWN_PEER

    def body(self) -> str:
        return self.msg or self.media or EMPTY_MESSAGE


@dataclass(slots=True)
class MenuConfig:
    """The reply menu the page hangs under a message."""

    links: list[str] = field(default_factory=list)
    links_per_menu: int = 3
    auto_clean_previous: bool = True

    def for_message(self, latest: bool) -> str:
        """The menu text, or empty when this message must not carry one.

        "Auto clean previous message menus: only the latest message keeps its
        menu while older menus are removed automatically" - so with the switch
        on, only the newest row renders links at all.
        """
        if not self.links:
            return ""
        if self.auto_clean_previous and not latest:
            return ""
        take = max(1, self.links_per_menu)
        return "\n".join(self.links[:take])


@dataclass(slots=True)
class TodoFilter:
    """``Filter service messages`` / ``Filter bot messages``."""

    hide_service: bool = True
    hide_bot: bool = True
    include_blacklisted: bool = False

    def keeps(self, record: TodoRecord) -> bool:
        if self.hide_service and record.is_service:
            return False
        if self.hide_bot and record.is_bot:
            return False
        return not (not self.include_blacklisted and record.is_blacklist)


def render(records: list[TodoRecord], filter_: TodoFilter,
           menu: MenuConfig | None = None, now: int = 0) -> list[str]:
    """One line per row, newest first, plus the menu on the newest only."""
    kept = [record for record in records if filter_.keeps(record)]
    kept.sort(key=lambda row: (row.priority, -row.last_msg_at, -row.id))
    lines: list[str] = []
    for index, record in enumerate(kept):
        when = ago(max(0, now - record.last_msg_at)) if now else UNKNOWN_TIME
        flag = " [blacklisted]" if record.is_blacklist else ""
        count = (f" x{record.unread_count}" if record.unread_count > 1 else "")
        lines.append(f"#{record.id} {record.sender_label()}{flag}{count} "
                     f"({when}) {record.body()}")
        if menu is not None:
            block = menu.for_message(latest=index == 0)
            if block:
                lines.extend(f"    {line}" for line in block.splitlines())
    return lines


__all__ = [
    "ARCHIVED",
    "DONE",
    "EMPTY_MESSAGE",
    "MenuConfig",
    "PENDING",
    "STATUS_NAMES",
    "TodoFilter",
    "TodoRecord",
    "UNKNOWN_PEER",
    "UNKNOWN_TIME",
    "ago",
    "render",
]
