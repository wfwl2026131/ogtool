"""#10 batch rename: the name parser and the pause/resume/stop state machine."""

from __future__ import annotations

from pathlib import Path

import pytest

from ogtool.config import ConfigError
from ogtool.features.batch_rename import (
    IDLE,
    PAUSED,
    STOPPED,
    RenameConfig,
    parse_names,
    run,
)


def test_parse_names_takes_one_or_two_parts():
    assert parse_names(["Alice", "Bob Smith", "  Carol   Van  "]) == [
        ("Alice", ""),
        ("Bob", "Smith"),
        ("Carol", "Van"),
    ]


def test_from_dict_reads_a_txt_file(tmp_path: Path):
    listing = tmp_path / "names.txt"
    listing.write_text("# comment\nAlice\nBob Smith\n", encoding="utf-8")
    config = RenameConfig.from_dict(
        {"batch_rename": {"file": str(listing)}}, None)  # type: ignore[arg-type]
    assert config.names == [("Alice", ""), ("Bob", "Smith")]


def test_missing_list_is_a_config_error():
    with pytest.raises(ConfigError):
        RenameConfig.from_dict({}, None)  # type: ignore[arg-type]


def test_run_walks_every_entry(ctx, capsys):
    config = RenameConfig(names=[("Alice", ""), ("Bob", "Smith")],
                          operation_interval=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "进度：2/2" in output
    assert "状态：空闲" in output
    assert "PLAN: 2 renames" in output


def test_pause_and_continue_are_printed(ctx, capsys):
    config = RenameConfig(names=[("Alice", "")], pause_at=1)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert f"状态：{PAUSED}" in output
    assert "（继续）" in output
    assert f"状态：{IDLE}" in output


def test_stop_leaves_the_state_at_stopped(ctx, capsys):
    config = RenameConfig(names=[("Alice", ""), ("Bob", "")], stop_at=1)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert f"状态：{STOPPED}" in output
    assert "进度：0/2" in output


def test_failure_is_counted(ctx, scripted, capsys):
    scripted(["USER_PRIVACY_RESTRICTED"])
    config = RenameConfig(names=[("Alice", "")])
    assert run(config, ctx) == 1
    output = capsys.readouterr().out
    assert "失败: 1" in output


def test_a_live_run_waits_the_rename_interval(ctx, live, ok_sender, slept):
    live(ok_sender)
    config = RenameConfig(names=[("Alice", ""), ("Bob", ""), ("Cid", "")],
                          operation_interval=7.0)
    assert run(config, ctx) == 0
    # Three renames, two gaps, each exactly the interval the page shows.
    assert slept == [7.0, 7.0]


def test_a_dry_run_renames_without_waiting(ctx, slept, capsys):
    config = RenameConfig(names=[("Alice", ""), ("Bob", "")],
                          operation_interval=7.0)
    assert run(config, ctx) == 0
    assert slept == []
    assert "waited: " not in capsys.readouterr().out


def test_flood_cap_retries_a_flooded_rename(ctx, scripted, capsys):
    """``floodCap`` is the rename page's PEER_FLOOD retry count."""
    scripted(["PEER_FLOOD", "PEER_FLOOD", "ok"])
    config = RenameConfig(names=[("Alice", "")], flood_cap=2)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "PEER_FLOOD backoff" in output
    assert "失败: 0" in output


def test_a_zero_flood_cap_does_not_retry(ctx, scripted, capsys):
    scripted(["PEER_FLOOD"])
    config = RenameConfig(names=[("Alice", "")], flood_cap=0)
    assert run(config, ctx) == 1
    assert "PEER_FLOOD backoff" not in capsys.readouterr().out


def test_entries_preview_shows_the_head_of_every_list(ctx, capsys):
    """``entriesPreview``: a destructive run shows what it is about to write."""
    config = RenameConfig(names=[("新名A", ""), ("新名B", ""), ("新名C", "")],
                          entries_preview=2, operation_interval=0.0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "昵称预览 01: 新名A" in output
    assert "昵称预览 02: 新名B" in output
    # The third name still runs, but the preview stops at two.
    assert "昵称预览 03" not in output
    assert "还有 1 条未显示" in output


def test_entries_preview_zero_prints_no_preview(ctx, capsys):
    config = RenameConfig(names=[("新名A", "")], entries_preview=0,
                          operation_interval=0.0)
    assert run(config, ctx) == 0
    assert "预览" not in capsys.readouterr().out


def test_parallel_mode_runs_one_lane_per_account(ctx, live, ok_sender, capsys):
    """``parallelMode``: each account edits its own lane at the same time."""
    live(ok_sender)
    config = RenameConfig(names=[("A", ""), ("B", ""), ("C", ""), ("D", "")],
                          parallel_mode=True, operation_interval=0.0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "parallel: 2 lanes, one account each" in output
    assert "PLAN: 4 renames" in output
    assert "failed 0" in output


def test_parallel_mode_is_off_by_default(ctx, capsys):
    config = RenameConfig(names=[("A", ""), ("B", "")],
                          operation_interval=0.0)
    assert run(config, ctx) == 0
    assert "parallel:" not in capsys.readouterr().out
