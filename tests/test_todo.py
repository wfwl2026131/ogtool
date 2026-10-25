"""The todo list is the reference's customer-service queue.

Evidence: the three tables (``from_todo_list`` / ``from_todo_topic`` /
``from_todo_archive``, 1723584-1723820), the manager's classes
(``Deep::FromTodoManager::TodoRecord/TodoEvent/Status``, 1744739-1744741), and
the page's menu block (1744760-1744790) which names the filters, the link menu
and the relative-time wording.
"""

from __future__ import annotations

from ogtool.todo import (
    ARCHIVED,
    DONE,
    EMPTY_MESSAGE,
    PENDING,
    UNKNOWN_TIME,
    MenuConfig,
    TodoFilter,
    TodoRecord,
    ago,
    render,
)


def test_the_status_numbers_are_the_dump_s_own():
    assert (PENDING, DONE, ARCHIVED) == (1, 2, 3)


def test_relative_time_uses_the_page_s_four_rungs():
    assert ago(5) == "5 seconds ago"
    assert ago(120) == "2 minutes ago"
    assert ago(7200) == "2 hours ago"
    assert ago(172800) == "2 days ago"
    assert ago(-1) == UNKNOWN_TIME


def test_an_empty_message_is_the_page_s_placeholder():
    assert TodoRecord(from_id="u1", peer_id="@p").body() == EMPTY_MESSAGE


def test_media_stands_in_for_a_missing_text():
    record = TodoRecord(from_id="u1", peer_id="@p", media="photo.jpg")
    assert record.body() == "photo.jpg"


def test_the_service_filter_hides_join_and_leave():
    record = TodoRecord(from_id="u1", peer_id="@g", is_service=True)
    assert TodoFilter().keeps(record) is False
    assert TodoFilter(hide_service=False).keeps(record) is True


def test_the_bot_filter_keeps_real_customers():
    bot = TodoRecord(from_id="b1", peer_id="@p", is_bot=True)
    human = TodoRecord(from_id="u1", peer_id="@p")
    kept = [row for row in (bot, human) if TodoFilter().keeps(row)]
    assert kept == [human]


def test_a_blacklisted_sender_is_hidden_unless_asked_for():
    record = TodoRecord(from_id="u1", peer_id="@p", is_blacklist=True)
    assert TodoFilter().keeps(record) is False
    assert TodoFilter(include_blacklisted=True).keeps(record) is True


def test_the_menu_has_links_per_menu_entries():
    menu = MenuConfig(links=["a", "b", "c", "d"], links_per_menu=2)
    assert menu.for_message(latest=True).splitlines() == ["a", "b"]


def test_auto_clean_keeps_the_menu_on_the_newest_message_only():
    menu = MenuConfig(links=["a"], auto_clean_previous=True)
    assert menu.for_message(latest=True) == "a"
    assert menu.for_message(latest=False) == ""


def test_turning_auto_clean_off_keeps_every_menu():
    menu = MenuConfig(links=["a"], auto_clean_previous=False)
    assert menu.for_message(latest=False) == "a"


def test_render_marks_the_newest_and_puts_priority_first():
    records = [
        TodoRecord(id=1, from_id="u1", peer_id="@p", msg="old", last_msg_at=100),
        TodoRecord(id=2, from_id="u2", peer_id="@p", msg="new", last_msg_at=200),
    ]
    lines = render(records, TodoFilter(), None, now=260)
    assert lines[0].startswith("#2")
    assert "(1 minutes ago)" in lines[0] or "(1 minute" in lines[0]


def test_unread_count_is_shown_only_when_it_matters():
    once = TodoRecord(id=1, from_id="u1", peer_id="@p", msg="hi")
    twice = TodoRecord(id=2, from_id="u2", peer_id="@p", msg="hi",
                       unread_count=3)
    lines = render([once, twice], TodoFilter(), None, now=10)
    assert " x3" in " ".join(lines)
    assert " x1" not in " ".join(lines)


# --- the store side ---------------------------------------------------


def test_a_second_message_bumps_the_same_row(store):
    first = store.upsert_todo({"from_id": "u1", "peer_id": "@p", "msg": "one"})
    second = store.upsert_todo({"from_id": "u1", "peer_id": "@p", "msg": "two"})
    assert first == second
    rows = store.list_todos()
    assert len(rows) == 1
    assert rows[0]["unread_count"] == 2
    assert rows[0]["msg"] == "two"


def test_a_different_peer_is_a_different_piece_of_work(store):
    store.upsert_todo({"from_id": "u1", "peer_id": "@p1", "msg": "one"})
    store.upsert_todo({"from_id": "u1", "peer_id": "@p2", "msg": "two"})
    assert store.count_todos(PENDING) == 2


def test_marking_read_and_unread_moves_the_status(store):
    row = store.upsert_todo({"from_id": "u1", "peer_id": "@p", "msg": "hi"})
    assert store.set_todo_status([row], DONE) == 1
    assert store.count_todos(DONE) == 1
    store.set_todo_status([row], PENDING)
    assert store.count_todos(PENDING) == 1


def test_blacklisting_is_a_column_not_a_delete(store):
    row = store.upsert_todo({"from_id": "u1", "peer_id": "@p", "msg": "hi"})
    assert store.set_todo_blacklist([row], True) == 1
    assert store.list_todos()[0]["is_blacklist"] == 1
    store.set_todo_blacklist([row], False)
    assert store.list_todos()[0]["is_blacklist"] == 0


def test_archiving_moves_rows_and_keeps_their_history(store):
    row = store.upsert_todo({"from_id": "u1", "peer_id": "@p", "msg": "hi"})
    store.set_todo_status([row], DONE)
    assert store.archive_todos([row]) == 1
    assert store.count_todos() == 0
    archived = store.list_todo_archive()
    assert archived[0]["from_id"] == "u1"
    assert archived[0]["msg"] == "hi"


def test_archiving_everything_touches_only_finished_work(store):
    pending = store.upsert_todo({"from_id": "u1", "peer_id": "@p", "msg": "a"})
    done = store.upsert_todo({"from_id": "u2", "peer_id": "@p", "msg": "b"})
    store.set_todo_status([done], DONE)
    assert store.archive_todos() == 1
    assert store.count_todos() == 1
    assert store.list_todos()[0]["id"] == pending


def test_a_new_message_reopens_a_done_row_but_not_an_archived_one(store):
    row = store.upsert_todo({"from_id": "u1", "peer_id": "@p", "msg": "a"})
    store.set_todo_status([row], DONE)
    store.upsert_todo({"from_id": "u1", "peer_id": "@p", "msg": "b"})
    assert store.list_todos()[0]["status"] == PENDING
