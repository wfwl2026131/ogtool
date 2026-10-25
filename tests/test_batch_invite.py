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


def test_a_live_run_waits_between_two_invites(ctx, live, ok_sender, slept):
    live(ok_sender)
    config = InviteConfig(groups=["@g1"], members=["@m1", "@m2", "@m3"],
                          strategy=STRATEGY_LOOP, request_interval=4.0,
                          invite_interval=9.0)
    assert run(config, ctx) == 0
    # The two intervals both pace one invite; the longer one wins.
    assert slept == [9.0, 9.0]


def test_a_dry_run_invites_without_waiting(ctx, slept, capsys):
    config = InviteConfig(groups=["@g1"], members=["@m1", "@m2"],
                          strategy=STRATEGY_LOOP, request_interval=4.0,
                          invite_interval=9.0)
    assert run(config, ctx) == 0
    assert slept == []
    assert "waited: " not in capsys.readouterr().out


def test_threads_split_the_queue_into_handler_lanes(ctx, scripted, capsys):
    """``threads`` was printed as 'concurrency: 4' and never used."""
    scripted(["ok", "ok", "ok", "ok"])
    config = InviteConfig(groups=["@g1"], members=["@m1", "@m2", "@m3", "@m4"],
                          threads=2, request_interval=0, invite_interval=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "threads: 2 lanes, one handler account each" in output
    assert "成功: 4" in output


def test_one_handler_never_appears_in_two_lanes():
    from ogtool.features._common import lanes_by

    jobs = [("h1", "@g1", "@m1"), ("h2", "@g1", "@m2"), ("h1", "@g1", "@m3")]
    lanes = lanes_by(lambda job: job[0], jobs, 3)
    seen = [handler for lane in lanes for handler, _, _ in lane]
    assert sorted(seen) == ["h1", "h1", "h2"]
    assert all(len({job[0] for job in lane}) == 1 for lane in lanes)


def test_handler_flood_cap_rests_a_handler(ctx, scripted, capsys):
    scripted(["PEER_FLOOD", "PEER_FLOOD", "PEER_FLOOD"])
    config = InviteConfig(groups=["@g1"], members=["@m1", "@m2"],
                          handler_flood_cap=1, member_flood_cap=0,
                          threads=1, request_interval=0, invite_interval=0)
    run(config, ctx)
    output = capsys.readouterr().out
    assert "handler rested: " in output
