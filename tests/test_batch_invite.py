"""#11 batch invite: strategy, caps, the two exports and the failure buckets."""

from __future__ import annotations

from pathlib import Path

import pytest

from ogtool.config import ConfigError
from ogtool.features.batch_invite import (
    STRATEGY_CONTINUE,
    STRATEGY_LOOP,
    InviteConfig,
    build_jobs,
    run,
)


def test_continue_strategy_resumes_where_the_last_group_stopped():
    jobs = build_jobs(["g1", "g2"], ["m1", "m2", "m3", "m4"], ["h"],
                      STRATEGY_CONTINUE, target_success_cap=2)
    assert [(group, member) for _, group, member in jobs] == [
        ("g1", "m1"), ("g1", "m2"),
        ("g2", "m3"), ("g2", "m4"),
    ]


def test_loop_strategy_starts_every_group_from_the_first_member():
    jobs = build_jobs(["g1", "g2"], ["m1", "m2"], ["h"],
                      STRATEGY_LOOP, target_success_cap=2)
    assert [(group, member) for _, group, member in jobs] == [
        ("g1", "m1"), ("g1", "m2"),
        ("g2", "m1"), ("g2", "m2"),
    ]


def test_handler_exec_cap_stops_the_queue():
    jobs = build_jobs(["g1", "g2"], ["m1", "m2", "m3"], ["h1", "h2"],
                      STRATEGY_CONTINUE, handler_exec_cap=1)
    assert len(jobs) == 2


def test_missing_and_flood_lists_are_exported(ctx, scripted, tmp_path: Path,
                                              capsys):
    scripted(["USER_PRIVACY_RESTRICTED", "PEER_FLOOD", "PEER_FLOOD",
              "PEER_FLOOD", "ok"])
    missing = tmp_path / "missing.txt"
    flood = tmp_path / "flood.txt"
    config = InviteConfig(
        groups=["@g1"], members=["@m1", "@m2"], strategy=STRATEGY_LOOP,
        target_success_cap=0, handler_flood_cap=0, member_flood_cap=0,
        export_missing=str(missing), export_flood=str(flood),
    )
    run(config, ctx)
    output = capsys.readouterr().out
    assert "MISSING: 1" in output
    assert "FLOOD: 1" in output
    assert missing.exists() and "@m1" in missing.read_text()
    assert flood.exists() and "@m2" in flood.read_text()


def test_missing_groups_is_a_config_error():
    with pytest.raises(ConfigError):
        InviteConfig.from_dict({"batch_invite": {"members": ["@m1"]}}, None)  # type: ignore[arg-type]


def test_missing_members_is_a_config_error():
    with pytest.raises(ConfigError):
        InviteConfig.from_dict({"batch_invite": {"groups": ["@g1"]}}, None)  # type: ignore[arg-type]


def test_success_counts_are_reported(ctx, capsys):
    config = InviteConfig(groups=["@g1"], members=["@m1", "@m2"],
                          strategy=STRATEGY_LOOP)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "成功: 2" in output
    assert "PLAN: 2 invites" in output
