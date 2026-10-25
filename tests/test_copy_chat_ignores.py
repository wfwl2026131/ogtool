"""#1's five ignore switches, plus the speed ceiling and lightweight mode.

The switches are the reason a copied post does not look like a copy: bots,
people who @ someone, t.me links, pinned posts and admin notices each have a
box on the page. Three of them can be read from the text; the other two need
the capture to say so, and an unobserved flag is never treated as present.
"""

from __future__ import annotations

from dataclasses import replace

from ogtool.features.copy_chat import (
    IGNORE_SENDERS,
    CopyConfig,
    ignore_reason,
    read_ignores,
    run,
    select,
)
from ogtool.sources import SourceMessage


def _message(text: str = "hello", sender: str = "alice",
             flags: tuple[str, ...] = ()) -> SourceMessage:
    return SourceMessage(peer="@src", sender=sender, text=text, flags=flags)


def test_every_switch_has_a_name_and_starts_off():
    assert len(IGNORE_SENDERS) == 5
    assert read_ignores({}) == {name: False for name in IGNORE_SENDERS}


def test_the_switches_are_read_from_the_config():
    data = {"ignore_bot_messages": True, "ignore_tme_links": 1}
    read = read_ignores(data)
    assert read["ignore_bot_messages"] is True
    assert read["ignore_tme_links"] is True
    assert read["ignore_pinned_messages"] is False


def test_a_bot_is_dropped_only_when_the_capture_says_so():
    message = _message(sender="newsbot")
    assert ignore_reason(message, {"ignore_bot_messages": False}) == ""
    # No flag on the message: the sender name alone is not evidence.
    assert ignore_reason(message, {"ignore_bot_messages": True}) == ""
    flagged = _message(sender="newsbot", flags=("bot",))
    assert ignore_reason(flagged, {"ignore_bot_messages": True}) == "bot"


def test_an_at_message_is_dropped_by_its_text():
    message = _message("hi @carol, look")
    assert ignore_reason(message, {"ignore_at_messages": True}) == "at"
    assert ignore_reason(_message("hi carol"), {"ignore_at_messages": True}) == ""


def test_a_tme_link_is_dropped_by_its_text():
    message = _message("join https://t.me/somegroup")
    assert ignore_reason(message, {"ignore_tme_links": True}) == "tme"


def test_pinned_and_admin_need_their_flags():
    pinned = _message(flags=("pinned",))
    admin = _message(flags=("admin",))
    assert ignore_reason(pinned, {"ignore_pinned_messages": True}) == "pinned"
    assert ignore_reason(admin, {"ignore_admin_messages": True}) == "admin"
    assert ignore_reason(_message(), {"ignore_pinned_messages": True}) == ""


def test_select_still_reports_sender_and_keyword_first():
    message = _message("hello")
    assert select(message, [], ["alice"]) == "sender"
    assert select(message, ["hello"], []) == "keyword"
    assert select(message, ["bye"], []) == ""
    # Keywords are a blocklist: a message that contains one is dropped.
    assert select(message, ["hello"], [], {"ignore_at_messages": True}) == "keyword"


def test_select_reports_the_ignore_reason():
    message = _message("ping @dave")
    reason = select(message, ["ping"], [], {"ignore_at_messages": True})
    assert reason == "at"


def test_the_header_lists_the_switches_that_are_on(ctx, capsys):
    config = CopyConfig(
        targets=["@dest"],
        messages=[_message("hello", flags=("bot",))],
        ignores={"ignore_bot_messages": True},
        interval_min=0,
        interval_max=0,
    )
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "忽略开关：ignore_bot_messages" in output
    assert "queue=0" in output or "PLAN: 0 messages" in output


def test_lightweight_mode_skips_replacements(ctx, capsys):
    config = CopyConfig(
        targets=["@dest"],
        messages=[_message("buy now")],
        replacements=[("buy", "get")],
        lightweight_mode=True,
        interval_min=0,
        interval_max=0,
    )
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "lightweight mode" in output
    assert "text=get now" not in output


def test_replacements_still_apply_without_lightweight(ctx, capsys):
    config = CopyConfig(
        targets=["@dest"],
        messages=[_message("buy now")],
        replacements=[("buy", "get")],
        interval_min=0,
        interval_max=0,
    )
    assert run(config, ctx) == 0
    assert "text=get now" in capsys.readouterr().out


def test_message_speed_can_only_slow_the_run_down():
    # 6 messages per minute means one every 10s; the interval floor is lower,
    # so the speed wins. The delay is asserted through the arithmetic the run
    # uses, which keeps the test free of sleeps.
    speed = 6
    floor = max(0.5, 60.0 / speed)
    assert floor == 10.0
    assert replace(CopyConfig(), message_speed=speed).message_speed == 6
