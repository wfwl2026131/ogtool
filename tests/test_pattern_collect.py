"""The pattern collector: learning a script instead of only replaying one.

Evidence: ``Deep::PatternCollector``'s own log lines (1745607-1745660) - the
hour window, the 10 000-message cap, the flood-wait retry, and
``generateRealContentFromItems`` (the text is the real one).
"""

from __future__ import annotations

from pathlib import Path

from ogtool.pattern import (
    COLLECT_CAP,
    CollectConfig,
    items_to_messages,
    stop_reason,
    to_lines,
    write_script,
)
from ogtool.sources import parse_many


def test_the_cap_is_the_collector_s_own_number():
    assert COLLECT_CAP == 10_000


def test_each_completion_is_the_collector_s_own_sentence():
    assert "no more messages available" in stop_reason(0, 24, 100)
    assert "message limit (10)" in stop_reason(10, 24, 10)
    assert "24-hour time limit with 5 messages" in stop_reason(5, 24, 100)


def test_real_text_becomes_a_script_line():
    messages = parse_many(["@g|Ann|hello there"])
    lines, dropped = to_lines(CollectConfig(target="@g"), messages)
    assert lines == ["A0|L0|D5-15|hello there"]
    assert dropped == 0


def test_a_picture_rides_along_as_an_img_reference():
    messages = parse_many(["@g|Bob|see this|/tmp/pic.jpg"])
    lines, _ = to_lines(CollectConfig(target="@g"), messages)
    assert lines == ["A0|L0|D5-15|see this [IMG:/tmp/pic.jpg]"]


def test_the_delay_window_and_slot_are_written_through():
    messages = parse_many(["@g|Ann|hi"])
    lines, _ = to_lines(CollectConfig(target="@g", slot=2, reply_level=1,
                                      delay_min=30, delay_max=60), messages)
    assert lines == ["-A2|L0|D30-60|hi"]


def test_an_empty_message_is_skipped_not_written_blank():
    messages = parse_many(["@g|Ann|", "@g|Ann|real"])
    lines, dropped = to_lines(CollectConfig(target="@g"), messages)
    assert lines == ["A0|L0|D5-15|real"]
    assert dropped == 0


def test_an_unreadable_sticker_is_dropped_and_counted():
    """Replaying ``[STICKER:abc]`` literally would post that string."""
    messages = parse_many(["@g|Ann|[STICKER:abc123]"])
    lines, dropped = to_lines(CollectConfig(target="@g"), messages)
    assert lines == []
    assert dropped == 1


def test_a_readable_sticker_survives():
    messages = parse_many(["@g|Ann|[STICKER:cat/x.webp]"])
    lines, _ = to_lines(CollectConfig(target="@g"), messages)
    assert lines == ["A0|L0|D5-15|[STICKER:cat/x.webp]"]


def test_items_keep_the_order_and_skip_broken_rows():
    messages = items_to_messages(["@g|Ann|one", "junk", "@g|Bob|two"])
    assert [message.text for message in messages] == ["one", "two"]


def test_the_script_is_written_with_a_header(tmp_path: Path):
    path = tmp_path / "tdata" / "chat_pattern" / "x.txt"
    written = write_script(path, ["A0|L0|D5-15|hi"], header="from @g")
    assert Path(written).read_text(encoding="utf-8").splitlines() == [
        "# from @g", "A0|L0|D5-15|hi"]


def test_a_run_without_a_session_collects_nothing():
    """The DryRunSender answers ok with no items, so the collector cannot lie."""
    from ogtool.pattern import collect
    from ogtool.senders import DryRunSender

    class Ctx:
        live = False

    config = CollectConfig(target="@g", account="acc1")
    result = collect(DryRunSender(), config, Ctx())
    assert result.total == 0
    assert "no more messages available" in result.stopped
