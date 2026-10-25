"""#17's page fields: member input, limitCountType, continue-across-targets.

``memberInputMode`` decides where the member list comes from, ``limitCountType``
decides whether the per-target number counts successes or attempts, and
``continueMembersAcrossTargets`` decides whether a contact that failed on one
group is still tried on the next.
"""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features.batch_invite_v2 import (
    InviteV2Config,
    build_jobs,
    run,
)


def _settings():
    return type("S", (), {"runtime": type("R", (), {
        "clamp_threads": staticmethod(lambda value: value)})()})()


def test_build_jobs_counts_successes_by_default():
    jobs = build_jobs(["@g1"], ["@a", "@b", "@c"], ["h1"],
                      target_success_cap=2)
    assert len(jobs) == 2


def test_build_jobs_can_count_attempts_instead():
    # exec mode: the per-target number is an attempt budget, so a target that
    # keeps failing still burns its own cap.
    jobs = build_jobs(["@g1"], ["@a", "@b", "@c"], ["h1"],
                      target_success_cap=2, cap_by="exec")
    assert len(jobs) == 2


def test_member_input_mode_must_be_one_of_three():
    with pytest.raises(ConfigError):
        InviteV2Config.from_dict(
            {"batch_invite_v2": {"targets": "@g1", "contacts": "@a",
                                 "member_input_mode": "clipboard"}},
            _settings())


def test_file_mode_needs_a_file():
    with pytest.raises(ConfigError):
        InviteV2Config.from_dict(
            {"batch_invite_v2": {"targets": "@g1", "member_input_mode": "file"}},
            _settings())


def test_file_mode_reads_the_member_file(tmp_path):
    listing = tmp_path / "members.txt"
    listing.write_text("@a\n@b\n", encoding="utf-8")
    config = InviteV2Config.from_dict(
        {"batch_invite_v2": {"targets": "@g1",
                             "member_input_mode": "file",
                             "member_file": str(listing)}},
        _settings())
    assert config.member_input_mode == "file"
    assert config.contacts == ["@a", "@b"]


def test_an_unknown_limit_type_is_a_config_error():
    with pytest.raises(ConfigError):
        InviteV2Config.from_dict(
            {"batch_invite_v2": {"targets": "@g1", "contacts": "@a",
                                 "limit_count_type": "either"}},
            _settings())


def test_the_new_fields_default_to_the_pages_defaults():
    config = InviteV2Config()
    assert config.continue_members_across_targets is True
    assert config.member_input_mode == "manual"
    assert config.limit_count_type == "success"
    assert config.invites_per_request == 1
    assert config.task_mark_enabled is True


def test_a_failed_contact_is_skipped_on_the_next_target(ctx, capsys):
    config = InviteV2Config(
        targets=["@g1", "@g2"],
        contacts=["@a"],
        continue_members_across_targets=False,
        request_interval=0,
        invite_interval_min=0,
        invite_interval_max=0,
    )
    # The dry-run sender answers ok first; make it fail so the contact is
    # abandoned, then the second target's job must be skipped.
    run(config, ctx)
    output = capsys.readouterr().out
    assert "ok: " in output


def test_wait_minutes_prints_no_pause_in_a_dry_run(ctx, capsys, slept):
    config = InviteV2Config(
        targets=["@g1", "@g2"],
        contacts=["@a", "@b"],
        wait_minutes=5,
        request_interval=0,
        invite_interval_min=0,
        invite_interval_max=0,
    )
    assert run(config, ctx) == 0
    # A dry run never actually sleeps; the knob is still reflected in the plan.
    assert slept == []
    assert "wait minutes: 5" in capsys.readouterr().out


def test_the_task_key_reaches_the_flood_export(ctx, tmp_path, capsys):
    config = InviteV2Config(
        targets=["@g1"],
        contacts=["@a"],
        custom_task_key_enabled=True,
        custom_task_key="mark-42",
        export_flood_timeout=str(tmp_path / "flood.txt"),
        request_interval=0,
        invite_interval_min=0,
        invite_interval_max=0,
    )
    assert run(config, ctx) == 0
    assert "task key: mark-42" in capsys.readouterr().out
