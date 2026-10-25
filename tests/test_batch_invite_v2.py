"""#17 batch invite v2: caps, the PEER_FLOOD/TIMEOUT export and success cap."""

from __future__ import annotations

from pathlib import Path

import pytest

from ogtool.config import ConfigError
from ogtool.features.batch_invite_v2 import InviteV2Config, build_jobs, run


def test_round_robin_handlers_over_contacts():
    jobs = build_jobs(["g1"], ["m1", "m2", "m3"], ["h1", "h2"])
    assert [handler for handler, _, _ in jobs] == ["h1", "h2", "h1"]


def test_per_target_cap_trims():
    jobs = build_jobs(["g1", "g2"], ["m1", "m2", "m3"], ["h"],
                      target_success_cap=1)
    assert [(group, member) for _, group, member in jobs] == [
        ("g1", "m1"), ("g2", "m1"),
    ]


def test_exec_cap_trims():
    jobs = build_jobs(["g1"], ["m1", "m2", "m3"], ["h"], exec_cap=2)
    assert len(jobs) == 2


def test_flood_and_timeout_are_exported(ctx, scripted, tmp_path: Path,
                                        capsys):
    # Two flood retries are spent on @c1 before the cap gives up, then
    # @c2 answers a timeout.
    scripted(["PEER_FLOOD", "PEER_FLOOD", "PEER_FLOOD", "TIMEOUT"])
    path = tmp_path / "flood_timeout.txt"
    config = InviteV2Config(targets=["@g1"], contacts=["@c1", "@c2"],
                            export_flood_timeout=str(path))
    run(config, ctx)
    output = capsys.readouterr().out
    assert "PEER_FLOOD: 1" in output
    assert "TIMEOUT: 1" in output
    body = path.read_text()
    assert "PEER_FLOOD\t@c1" in body
    assert "TIMEOUT\t@c2" in body


def test_success_cap_stops_the_queue(ctx, capsys):
    config = InviteV2Config(targets=["@g1"], contacts=["@c1", "@c2", "@c3"],
                            success_cap=1)
    run(config, ctx)
    output = capsys.readouterr().out
    assert "stopped: success_cap (1)" in output


def test_missing_target_is_a_config_error():
    with pytest.raises(ConfigError):
        InviteV2Config.from_dict(
            {"batch_invite_v2": {"contacts": ["@c1"]}}, None)  # type: ignore[arg-type]


def test_a_live_run_waits_the_invite_interval(ctx, live, ok_sender, slept):
    live(ok_sender)
    config = InviteV2Config(targets=["@g1"], contacts=["@c1", "@c2", "@c3"],
                            request_interval=4.0, invite_interval_min=6.0,
                            invite_interval_max=6.0)
    assert run(config, ctx) == 0
    assert slept == [6.0, 6.0]


def test_a_dry_run_invites_without_waiting(ctx, slept):
    config = InviteV2Config(targets=["@g1"], contacts=["@c1", "@c2"],
                            request_interval=4.0, invite_interval_min=6.0,
                            invite_interval_max=9.0)
    assert run(config, ctx) == 0
    assert slept == []


def test_a_target_is_rested_after_its_own_flood_ceiling(ctx, scripted, capsys):
    """``peerFloodTargetMaxRetries`` is a per-TARGET ceiling (dump 1744470).

    The per-invitee cap already existed; this one is about the group: when a
    target keeps answering PEER_FLOOD, the rest of its contacts are skipped
    instead of being spent on a target that is plainly closed for now.
    """
    scripted(["PEER_FLOOD", "PEER_FLOOD", "PEER_FLOOD"])
    config = InviteV2Config(targets=["@g1"], contacts=["@c1", "@c2", "@c3"],
                            peer_flood_target_max_retries=1,
                            invitee_flood_cap=0)
    run(config, ctx)
    output = capsys.readouterr().out
    assert "target rested: @g1" in output
    assert "skipped @c2 in @g1" in output
    assert "skipped @c3 in @g1" in output


def test_a_zero_target_ceiling_never_rests_a_target(ctx, scripted, capsys):
    scripted(["PEER_FLOOD", "ok"])
    config = InviteV2Config(targets=["@g1"], contacts=["@c1", "@c2"],
                            peer_flood_target_max_retries=0,
                            invitee_flood_cap=0)
    run(config, ctx)
    output = capsys.readouterr().out
    assert "target rested" not in output
    assert "-> ok" in output


def test_a_handler_is_rested_after_its_own_flood_ceiling(ctx, scripted, capsys):
    """``floodCap`` is the handler's patience, not the invitee's retry count.

    ``inviteeFloodCap`` says how many times one contact may be retried;
    ``floodCap`` says after how many PEER_FLOOD answers the handler itself stops
    for the rest of the run (dump: separate rows on the page).
    """
    scripted(["PEER_FLOOD", "PEER_FLOOD", "PEER_FLOOD"])
    config = InviteV2Config(targets=["@g1"], contacts=["@c1", "@c2", "@c3"],
                            flood_cap=1, invitee_flood_cap=0,
                            peer_flood_target_max_retries=0)
    run(config, ctx)
    output = capsys.readouterr().out
    # Round robin puts @c1 and @c3 on acc1, @c2 on acc2: the rested handler is
    # the one whose remaining contact gets skipped.
    assert "handler rested: acc1" in output
    assert "skipped @c3 in @g1 (handler rested" in output


def test_a_zero_handler_ceiling_never_rests_it(ctx, scripted, capsys):
    scripted(["PEER_FLOOD", "ok"])
    config = InviteV2Config(targets=["@g1"], contacts=["@c1", "@c2"],
                            flood_cap=0, invitee_flood_cap=0,
                            peer_flood_target_max_retries=0)
    run(config, ctx)
    assert "handler rested" not in capsys.readouterr().out
