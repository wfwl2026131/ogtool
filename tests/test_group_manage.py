"""#T2 group manage: the transfer flow, its verdict lines and its table."""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features.group_manage import GroupConfig, run


def make_config(**overrides: object) -> GroupConfig:
    raw: dict = {
        "accounts": "acc1",
        "target": "acc2",
        "groups": "@sweep",
        "password": "pw",
    }
    raw.update(overrides)
    return GroupConfig.from_dict({"group_manage": raw}, None)  # type: ignore[arg-type]


def test_a_transfer_prints_every_verdict_line(ctx, capsys):
    config = make_config()
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "transfer_owner_and_leave: task=" in output
    assert "account=acc1 group=@sweep" in output
    assert "Original owner left group @sweep." in output
    assert "Transferred group @sweep from acc1 to acc2 (channel)" in output
    assert "auto_transfer_ok:" in output and "left=1" in output
    assert "Transferred 1 groups" in output
    assert "Transfer task finished." in output
    assert "PLAN: 1 groups, 1 transferred, 0 failed" in output


def test_a_transfer_updates_the_group_mgr_row(ctx):
    config = make_config()
    assert run(config, ctx) == 0
    row = ctx.store.group_owner("@sweep")
    assert row is not None
    # The bound target resolves to its own phone, the old owner's phone is
    # empty in the test pool, and the chat is a megagroup-capable kind.
    assert row["owner_phone"] == "acc2"
    assert row["old_owner"] == ""
    assert row["status"] == "transferred"
    assert row["chat_type"] == 2


def test_a_dry_run_plans_instead_of_sending(ctx):
    config = make_config()
    assert run(config, ctx) == 0
    # Every ok row is a plan: nothing was sent in a dry run.
    rows = ctx.store.results_for_feature("group_manage")
    assert [row["status"] for row in rows] == ["planned"]


def test_missing_second_password_skips_the_account(ctx, capsys):
    config = make_config(password="")
    assert run(config, ctx) == 1
    output = capsys.readouterr().out
    assert "Skip account acc1: missing second password." in output
    assert "PLAN: 1 groups, 0 transferred, 1 failed" in output


def test_an_unparseable_group_is_skipped_with_a_line(ctx, capsys):
    config = make_config(groups="this is not a group")
    assert run(config, ctx) == 1
    output = capsys.readouterr().out
    assert "Invalid group id this is not a group, skip transfer." in output
    assert "PLAN: 1 groups, 0 transferred, 1 failed" in output


def test_a_failed_join_stops_that_group(ctx, scripted, capsys):
    scripted(["PEER_FLOOD"])
    config = make_config()
    assert run(config, ctx) == 1
    output = capsys.readouterr().out
    assert "Target account failed to join group @sweep before transfer." in output
    assert "PLAN: 1 groups, 0 transferred, 1 failed" in output


def test_a_failed_ownership_edit_is_counted(ctx, scripted, capsys):
    scripted(["OK", "PEER_FLOOD"])
    config = make_config()
    assert run(config, ctx) == 1
    output = capsys.readouterr().out
    assert "Transfer owner failed for group @sweep: peer_flood" in output
    assert "auto_transfer_failed:" in output and "err=PEER_FLOOD" in output
    assert "PLAN: 1 groups, 0 transferred, 1 failed" in output


def test_a_failed_leave_still_counts_the_transfer(ctx, scripted, capsys):
    scripted(["OK", "OK", "PEER_FLOOD"])
    config = make_config()
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "Original owner failed to leave group @sweep." in output
    assert "auto_transfer_ok:" in output and "left=0" in output
    assert "Transferred group @sweep from acc1 to acc2 (channel)" in output
    assert "PLAN: 1 groups, 1 transferred, 0 failed" in output


def test_an_outside_target_is_invited_by_the_source(ctx, capsys):
    config = make_config(target="@outsider")
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "Transferred group @sweep from acc1 to @outsider (channel)" in output


def test_from_store_takes_the_groups_the_owner_holds(ctx, capsys):
    ctx.store.upsert_group_owner(
        group_id="-1001234", owner_phone="", status="transferred", chat_type=2
    )
    config = make_config(groups="", from_store=True)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "group=-1001234" in output
    assert "groups: 0 + store" in output


def test_store_groups_and_store_rows_dedupe(ctx, capsys):
    ctx.store.upsert_group_owner(
        group_id="-1001234", owner_phone="", status="transferred", chat_type=2
    )
    config = make_config(groups="-1001234", from_store=True)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    # The box and the store both name the group; the plan keeps it once.
    assert "group=-1001234" in output
    assert output.count("transfer_owner_and_leave: task=") == 1
    assert "PLAN: 1 groups" in output


def test_a_live_run_pauses_between_groups(ctx, live, ok_sender, slept):
    live(ok_sender)
    config = GroupConfig.from_dict(
        {
            "group_manage": {
                "accounts": "acc1",
                "target": "acc2",
                "groups": "@one,@two",
                "password": "pw",
                "interval_ms": 250,
            }
        },
        None,
    )  # type: ignore[arg-type]
    assert run(config, ctx) == 0
    assert slept == [0.25]


def test_a_dry_run_does_not_pause(ctx, slept):
    config = make_config(groups="@one,@two", interval_ms=250)
    assert run(config, ctx) == 0
    assert slept == []


def test_no_source_account_binds_the_whole_pool(ctx, capsys):
    """Omitting --accounts is the CLI's "whole pool" default, not an error."""
    config = GroupConfig.from_dict(
        {"group_manage": {"target": "acc2", "groups": "@g", "password": "pw"}},
        None,  # type: ignore[arg-type]
    )
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "accounts: 2" in output
    assert "PLAN: 1 groups, 1 transferred, 0 failed" in output


def test_no_target_is_a_config_error():
    with pytest.raises(ConfigError):
        GroupConfig.from_dict(
            {"group_manage": {"accounts": "acc1", "groups": "@g"}}, None
        )


def test_no_groups_anywhere_is_a_config_error():
    with pytest.raises(ConfigError):
        GroupConfig.from_dict(
            {"group_manage": {"accounts": "acc1", "target": "acc2"}}, None
        )


def test_the_password_can_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("GM_PW", "s3cret")
    config = GroupConfig.from_dict(
        {
            "group_manage": {
                "accounts": "acc1",
                "target": "acc2",
                "groups": "@g",
                "password_env": "GM_PW",
            }
        },
        None,
    )
    assert config.password == "s3cret"


def test_a_groups_file_is_merged_behind_the_box(tmp_path):
    group_file = tmp_path / "groups.txt"
    group_file.write_text("@one\n@two\n", encoding="utf-8")
    config = GroupConfig.from_dict(
        {
            "group_manage": {
                "accounts": "acc1",
                "target": "acc2",
                "groups": "@zero",
                "groups_file": str(group_file),
            }
        },
        None,
    )
    assert config.groups == ["@zero", "@one", "@two"]


def test_the_store_keeps_one_row_per_group(ctx):
    store = ctx.store
    store.upsert_group_owner(group_id="@g1", owner_phone="a", old_owner="b")
    store.upsert_group_owner(group_id="@g1", owner_phone="a2", old_owner="b2")
    store.upsert_group_owner(group_id="@g2", owner_phone="a", old_owner="b")
    assert store.group_owner("@g1")["owner_phone"] == "a2"
    assert [row["group_id"] for row in store.groups_for_owner("a")] == ["@g2"]
