"""#10's four lists: the sources, the modes, the probability, the avatars.

The nickname list is the one that existed before; these tests pin the three
that came with the page, plus the rule that decides whether a list contributes
an edit at all.
"""

from __future__ import annotations

import random

import pytest

from ogtool.config import ConfigError
from ogtool.features.batch_rename import (
    ABOUT_EMPTY,
    AVATAR_EMPTY,
    FILE,
    MANUAL,
    PROBABILITY_MISS,
    RenameConfig,
    RenameSource,
    _source,
    avatar_files,
    run,
)


def test_a_source_reads_manual_rows_and_a_file(tmp_path):
    listing = tmp_path / "about.txt"
    listing.write_text("first bio\nsecond bio\n", encoding="utf-8")
    data = {
        "about_mode": FILE,
        "about_manual": ["typed one"],
        "about_file": str(listing),
    }
    source = _source(data, "about")
    assert source.mode == FILE
    assert source.entries == ["typed one", "first bio", "second bio"]
    assert source.file == str(listing)


def test_manual_mode_keeps_only_the_typed_rows(tmp_path):
    listing = tmp_path / "about.txt"
    listing.write_text("from file\n", encoding="utf-8")
    data = {"about_mode": MANUAL, "about_manual": ["typed"], "about_file": str(listing)}
    assert _source(data, "about").entries == ["typed"]


def test_the_nickname_list_still_answers_to_the_old_keys(tmp_path):
    listing = tmp_path / "names.txt"
    listing.write_text("Alice\n", encoding="utf-8")
    # No nickname_* key at all: the plain file / entries pair carries it.
    source = _source({"file": str(listing)}, "nickname",
                     alias_file=str(listing), alias_entries=["Bob"])
    # The old shape fed both keys into one list, in that order: entries first,
    # then whatever the file holds. That order is kept.
    assert source.entries == ["Bob", "Alice"]
    assert source.mode == FILE


def test_a_bad_mode_is_a_config_error():
    with pytest.raises(ConfigError):
        _source({"about_mode": "clipboard"}, "about")


def test_an_unknown_source_mode_defaults_by_what_is_there(tmp_path):
    listing = tmp_path / "u.txt"
    listing.write_text("neo\n", encoding="utf-8")
    assert _source({"username_file": str(listing)}, "username").mode == FILE
    assert _source({}, "username").mode == MANUAL


def test_avatar_folder_takes_images_in_order(tmp_path):
    (tmp_path / "b.png").write_bytes(b"")
    (tmp_path / "a.jpg").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")
    assert [p.rsplit("/", 1)[-1] for p in avatar_files(str(tmp_path))] == [
        "a.jpg", "b.png",
    ]


def test_a_single_avatar_file_is_accepted(tmp_path):
    one = tmp_path / "only.webp"
    one.write_bytes(b"")
    assert avatar_files(str(one)) == [str(one)]


def test_a_missing_avatar_folder_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        avatar_files(str(tmp_path / "nope"))


def test_probability_sixty_skips_some_and_never_flips_itself():
    source = RenameSource(entries=["neo"], probability=60)
    rng = random.Random(7)
    outcomes = [source.misses(rng) for _ in range(200)]
    assert any(outcomes) and not all(outcomes)
    assert RenameSource(entries=["neo"], probability=0).misses(rng) is True
    assert RenameSource(entries=["neo"], probability=100).misses(rng) is False


def test_the_gap_prefers_the_pages_interval_ms():
    assert RenameConfig(interval_ms=2500, operation_interval=10.0).gap() == 2.5
    assert RenameConfig(interval_ms=0, operation_interval=10.0).gap() == 10.0


def test_the_four_lists_are_reported(ctx, capsys):
    config = RenameConfig(
        names=[("Alice", ""), ("Bob", "")],
        about=RenameSource(entries=["bio one", "bio two"]),
        usernames=RenameSource(entries=["neo", "trinity"]),
        avatars=RenameSource(entries=[]),
        operation_interval=0,
    )
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "四份清单：昵称 2  简介 2  头像 0  用户名 2" in output
    assert "简介已更新" in output
    assert "用户名已更新: @neo" in output
    assert "细节：昵称 2  简介 2  用户名 2  跳过 0" in output


def test_an_empty_avatar_entry_is_skipped_by_name(ctx, capsys):
    config = RenameConfig(
        names=[("Alice", "")],
        avatars=RenameSource(entries=[""]),
        operation_interval=0,
    )
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert AVATAR_EMPTY in output
    assert ABOUT_EMPTY not in output


def test_a_username_probability_of_zero_reports_every_miss(ctx, capsys):
    config = RenameConfig(
        names=[("Alice", ""), ("Bob", "")],
        usernames=RenameSource(entries=["neo"], probability=0),
        operation_interval=0,
    )
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert output.count(PROBABILITY_MISS) == 2


def test_a_blank_nickname_is_skipped_not_renamed(ctx, capsys):
    config = RenameConfig(names=[("", "")], operation_interval=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "昵称为空" in output
    assert "PLAN: 0 renames" in output
