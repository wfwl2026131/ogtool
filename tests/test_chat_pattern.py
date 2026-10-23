"""The chat_pattern grammar: four fields, a reply prefix and two media tags."""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from ogtool.chat_pattern import (
    PatternError,
    Sticker,
    assign_slots,
    import_zip,
    load,
    multiply,
    parse_line,
    parse_lines,
    parse_text,
)


def test_full_line_parses_every_field():
    message = parse_line("A4|L7|D6-12|hello there")
    assert message is not None
    assert message.account_slot == 4
    assert message.like_count == 7
    assert (message.delay_min, message.delay_max) == (6.0, 12.0)
    assert message.reply_level == 0
    assert message.text == "hello there"


def test_single_value_delay_becomes_a_window_of_one():
    message = parse_line("A9|L0|D9-9|body")
    assert message is not None
    assert (message.delay_min, message.delay_max) == (9.0, 9.0)
    # The reference writes D600-600 for one value; a bare D600 means the same.
    shorthand = parse_line("-A59|L0|D600|")
    assert shorthand is not None
    assert (shorthand.delay_min, shorthand.delay_max) == (600.0, 600.0)


def test_comment_and_blank_lines_are_skipped():
    assert parse_line("# Account 4: demo1 (ID: 100)") is None
    assert parse_line("   ") is None
    assert parse_line("") is None


def test_multi_level_reply_prefix_counts_the_dashes():
    message = parse_line("--A15|L0|D22|")
    assert message is not None
    assert message.reply_level == 2
    assert message.text == ""
    assert message.sticker is None and message.image == ""


def test_sticker_and_image_are_split_out_of_the_body():
    message = parse_line("A8|L0|D43|btc [IMG:84.jpg]")
    assert message is not None
    assert message.text == "btc"
    assert message.image == "84.jpg"
    assert message.sticker is None

    sticker = parse_line("A4|L0|D6|[STICKER:Set_Name/emoji.webp]")
    assert sticker is not None
    assert sticker.text == ""
    assert sticker.sticker is not None
    assert sticker.sticker.short_name == "Set_Name"
    assert sticker.sticker.emoji == "emoji.webp"


def test_leading_space_after_a_tag_is_tolerated():
    message = parse_line("A22|L0|D2-8| [STICKER:unknown/317/106/.webp]")
    assert message is not None
    assert message.text == ""
    assert message.sticker is not None
    assert message.sticker.set_id == "317"
    assert message.sticker.access_hash == "106"


def test_all_three_sticker_spellings_parse():
    short = Sticker.parse("Parallel_Halibut_by_fStikBot/.webp")
    assert short.short_name == "Parallel_Halibut_by_fStikBot"
    full = Sticker.parse("unknown/317/106/.webp")
    assert (full.set_id, full.access_hash) == ("317", "106")
    early = Sticker.parse("unknown/317/.webp")
    assert early.set_id == "317" and early.access_hash == ""


def test_missing_segment_names_the_line():
    with pytest.raises(PatternError) as caught:
        parse_lines(["A4|L0|D6"], source="demo.txt")
    assert "demo.txt" in str(caught.value)
    assert "four fields" in str(caught.value)


def test_broken_field_says_what_is_wrong():
    with pytest.raises(PatternError):
        parse_line("X4|L0|D6|body")
    with pytest.raises(PatternError):
        parse_line("A4|Q0|D6|body")
    with pytest.raises(PatternError):
        parse_line("A4|L0|Dx|body")


def test_render_round_trips_through_the_parser():
    original = parse_line("--A15|L3|D22-30|hi [IMG:a.jpg]")
    assert original is not None
    again = parse_line(original.render())
    assert again is not None
    assert again.account_slot == 15
    assert again.reply_level == 2
    assert again.like_count == 3
    assert (again.delay_min, again.delay_max) == (22.0, 30.0)
    assert again.text == "hi"
    assert again.image == "a.jpg"


def test_slots_are_assigned_in_first_appearance_order():
    messages = parse_text("A4|L0|D1|x\nA9|L0|D1|y\nA4|L0|D1|z")
    slots = assign_slots(messages, ["acc1", "acc2"])
    assert slots == {4: "acc1", 9: "acc2"}


def test_multiplier_repeats_in_place():
    messages = parse_text("A4|L0|D1|one\nA5|L0|D1|two")
    repeated = multiply(messages, 2)
    assert [message.text for message in repeated] == ["one", "one", "two", "two"]


def test_load_reads_a_file_and_rejects_an_empty_one(tmp_path: Path):
    path = tmp_path / "s.txt"
    path.write_text("A4|L0|D1|ok\n", encoding="utf-8")
    assert len(load(path)) == 1

    empty = tmp_path / "empty.txt"
    empty.write_text("# only a comment\n", encoding="utf-8")
    with pytest.raises(PatternError):
        load(empty)


def test_import_zip_takes_the_first_txt_and_merges_pic(tmp_path: Path):
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("script.txt", "A4|L0|D1|hello\n")
        bundle.writestr("readme.txt", "second")
        bundle.writestr("pic/one.jpg", b"jpg")
        bundle.writestr("deep/two.jpg", b"jpg")

    saved = import_zip(archive, tmp_path / "patterns", tmp_path / "pics",
                       now=datetime(2026, 10, 23, 16, 30, 0))
    assert saved.name == "script_20261023_163000.txt"
    assert saved.read_text(encoding="utf-8").startswith("A4|")
    assert (tmp_path / "pics" / "one.jpg").exists()
    assert not (tmp_path / "pics" / "two.jpg").exists()


def test_import_zip_without_a_txt_is_an_error(tmp_path: Path):
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("pic/one.jpg", b"jpg")
    with pytest.raises(PatternError) as caught:
        import_zip(archive, tmp_path / "p", tmp_path / "i")
    assert "No txt pattern file found" in str(caught.value)
