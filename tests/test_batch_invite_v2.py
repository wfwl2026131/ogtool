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
