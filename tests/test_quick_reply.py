"""#T5 QuickReply: shortcut slots and the page's own send verdicts.

The page's two lists (categories / quick messages), the ``/[1-8]`` slot
grammar (:1741728), the selection refusals (:1741745-1741746), and the
send pair (:1741736-1741743) with the fact block (:1741737-1741740).
"""

from __future__ import annotations

from ogtool.features.quick_reply import (
    QuickReplyConfig,
    run,
    slot_error,
)


def _config(**overrides) -> QuickReplyConfig:
    values: dict = {"task_name": "t"}
    values.update(overrides)
    return QuickReplyConfig(**values)


def test_the_slot_grammar_is_the_page_own():
    for slot in ("/1", "/5", "/8"):
        assert slot_error(slot) is None
    for bad in ("", "/0", "/9", "//1", "1", "/12"):
        assert slot_error(bad) is not None


def test_add_message_fills_the_slot(store, ctx, capsys):
    run(_config(add_message=["greetings|/1|hello there"]), ctx)
    out = capsys.readouterr().out
    assert "New quick message: greetings /1" in out
    row = store.quick_reply_get("greetings", "/1")
    assert row is not None and row["text"] == "hello there"


def test_a_bad_slot_refuses_the_add(store, ctx, capsys):
    assert run(_config(add_message=["greetings|/9|hi"]), ctx) == 1
    assert "Invalid quick message slot: /9" in capsys.readouterr().out
    assert store.quick_reply_get("greetings", "/9") is None


def test_readding_a_slot_replaces_its_text(store, ctx):
    run(_config(add_message=["g|/1|first", "g|/1|second"]), ctx)
    assert store.quick_reply_get("g", "/1")["text"] == "second"


def test_delete_names_the_slot(store, ctx, capsys):
    run(_config(add_message=["g|/2|bye"]), ctx)
    capsys.readouterr()
    assert run(_config(delete_message=["g|/2"]), ctx) == 0
    assert "Deleted: g /2" in capsys.readouterr().out
    assert run(_config(delete_message=["g|/2"]), ctx) == 1
    assert "Failed to delete: g /2" in capsys.readouterr().out


def test_the_empty_page_lists_nothing(store, ctx, capsys):
    run(_config(), ctx)
    out = capsys.readouterr().out
    assert "quick reply: 0 slot(s)" in out


def test_a_send_without_a_target_names_the_chat_refusal(store, ctx, capsys):
    run(_config(add_message=["g|/1|hi"]), ctx)
    capsys.readouterr()
    assert run(_config(send="g|/1"), ctx) == 1
    assert "Please select a chat first" in capsys.readouterr().out


def test_a_send_of_an_empty_slot_names_the_window_refusal(store, ctx, capsys):
    assert run(_config(send="g|/1", target="@t1"), ctx) == 1
    assert "Please select a window first" in capsys.readouterr().out


def test_a_dry_send_plans_without_sending(store, ctx, capsys):
    run(_config(add_message=["g|/1|hello"]), ctx)
    capsys.readouterr()
    assert run(_config(send="g|/1", target="@t1"), ctx) == 0
    out = capsys.readouterr().out
    assert "Sending quick reply to" in out
    assert "dry-run: no message was sent" in out


def test_a_live_send_prints_the_page_fact_block(store, live, ok_sender, capsys):
    store.upsert_quick_reply("g", "/3", "hello live")
    config = _config(account_labels=["acc1"], send="g|/3", target="@t1")
    assert run(config, live(ok_sender)) == 0
    out = capsys.readouterr().out
    assert "Message sent successfully!" in out
    assert "Sent to" in out and "(ID:" in out
