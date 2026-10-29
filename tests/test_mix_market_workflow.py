"""The v1.2.1 workflow alignment: the page's own step names, the per-step
permission gates, ``heldTargets`` across runs and the ``inputFilePath``.

Every line these tests pin is copied from the reference's workflow strings
(:1741560-:1741650): ``Workflow:`` with the five numbered steps,
``<Step>: No permission``, ``heldTargets``, ``Invalid rest range``,
``Please select an input file`` / ``Input file not found: %1`` /
``Input file is empty``, ``Dead targets detected, rebuild accounts: %1``,
``GroupMgr config missing, use defaults.``, the per-account ``A%1`` logs and
``[Speech]`` / ``[Invite]`` / ``Mass Send DM v2 completed``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ogtool.config import ConfigError
from ogtool.features import mix_market
from ogtool.features.mix_market import (
    HELD_FILE,
    STAGES,
    HeldTargets,
    MixConfig,
    run,
)


def make(pattern: str | None = None, **overrides) -> MixConfig:
    values = {
        "group_titles": ["Market A"],
        "pattern": pattern or "",
        "text": "hello from the workflow",
        "contacts": ["Ann|@ann", "Bob|@bob"],
        "activity": {},
        "dead_check_interval": 30.0,
        "dead_after_hours": 72.0,
        "create_rest_min": 1.0,
        "create_rest_max": 5.0,
        "stop_after": len(STAGES),
    }
    values.update(overrides)
    return MixConfig(**values)


def test_the_workflow_block_names_the_reference_steps(ctx, pattern_file,
                                                      capsys):
    ctx.script.codes = ["ok"] * 40
    assert run(make(pattern=pattern_file(["A4|L0|D6|hello"])), ctx) == 0
    out = capsys.readouterr().out
    assert "1) Batch Contacts" in out
    assert "2) Create owned group/channel" in out
    assert "3) Puppet Chat v2" in out
    assert "4) Batch Pull v2 (pull own contacts)" in out
    assert "5) Mass Send DM v2 (to own contacts)" in out
    assert "Workflow started" in out
    assert "Workflow stopped" in out


def test_an_inverted_rest_range_is_rejected():
    with pytest.raises(ConfigError, match="Invalid rest range"):
        mix_market.run(make(create_rest_min=6.0, create_rest_max=1.0), None)

def test_the_input_file_is_merged_one_contact_per_line(ctx, tmp_path, capsys):
    inbox = tmp_path / "input.txt"
    inbox.write_text("Cara|@cara\nDana|@dana\n", encoding="utf-8")
    ctx.script.codes = ["ok"] * 10
    config = make(contacts=["Ann|@ann"], contacts_file=str(inbox),
                  stop_after=1)
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    # The file lands behind the --contact entries, first occurrence wins.
    assert config.contacts == ["Ann|@ann", "Cara|@cara", "Dana|@dana"]
    # Three contacts over two accounts: round robin puts Cara on A2.
    assert "A2 added: Cara|@cara" in out


def test_a_missing_input_file_is_the_page_error(ctx):
    config = make(contacts_file=str(Path(ctx.settings.data_dir) / "nope.txt"))
    with pytest.raises(ConfigError, match="Input file not found"):
        run(config, ctx)


def test_an_empty_input_file_is_the_page_error(ctx, tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("\n \n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Input file is empty"):
        run(make(contacts_file=str(empty)), ctx)


def test_permission_refusals_gate_the_contacts_stage(ctx, capsys):
    ctx.script.codes = ["CHAT_WRITE_FORBIDDEN"] * 2
    config = make(stop_after=1)
    # A gated step did not fail work: the run continues clean.
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "Batch Contacts: No permission" in out
    assert "stage 1/5 add_contact: ok=0 failed=0 skipped=2" in out


def test_permission_refusals_gate_the_group_manager(ctx, capsys):
    # Two contacts pass, then every create (first round and the held retry)
    # is refused: the step is gated, not failed.
    ctx.script.codes = ["ok", "ok", "CHAT_ADMIN_REQUIRED",
                        "CHAT_ADMIN_REQUIRED"]
    config = make(stop_after=2)
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "Group Manager: No permission" in out
    assert "stage 2/5 create_group: ok=0 failed=0 skipped=1" in out
    # The refused title stays on file for the next run.
    kept = HeldTargets(Path(ctx.settings.data_dir))
    assert kept.items == ["Market A"]
    assert "heldTargets: 1 target(s) kept on file for the next run" in out


def test_the_mass_send_stage_gates_on_permission(ctx, capsys):
    # adds x2 ok, pulls x2 ok, sends x2 refused: only stage 5 is gated.
    ctx.script.codes = ["ok"] * 4 + ["USER_PRIVACY_RESTRICTED"] * 2
    config = make(group_titles=[], targets=["@existing"])
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "Mass Send DM v2: No permission" in out
    assert "Mass Send DM v2 completed" not in out


def test_held_targets_carry_over_to_the_next_run(ctx, capsys):
    # Run one: both adds pass, the create fails for good, the title is kept.
    ctx.script.codes = ["ok", "ok", "FLOOD_WAIT_30", "FLOOD_WAIT_30"]
    assert run(make(group_titles=["Market A"], stop_after=2), ctx) == 1
    first = HeldTargets(Path(ctx.settings.data_dir))
    assert first.items == ["Market A"]
    # Run two: the held title comes back ahead of the fresh list, the create
    # succeeds and the file is cleared.
    ctx.script.codes = ["ok", "ok", "ok", "ok"]
    capsys.readouterr()
    assert run(make(group_titles=["Fresh B"], stop_after=2), ctx) == 0
    out = capsys.readouterr().out
    assert ("held: 1 target(s) restored from "
            "mix_market_held_targets.json: Market A") in out
    assert "A1 creating owned Market A..." in out
    assert HeldTargets(Path(ctx.settings.data_dir)).items == []
    assert not (Path(ctx.settings.data_dir) / HELD_FILE).exists()


def test_dead_targets_print_the_rebuild_line(ctx, capsys):
    config = make(pattern=None, group_titles=["Market A", "Market B"],
                  activity={"@og_market_b": 96.0})
    run(config, ctx)
    out = capsys.readouterr().out
    assert "Dead targets detected, rebuild accounts: @og_market_b" in out


def test_stage_two_logs_creating_owned_and_target_created(ctx, capsys):
    ctx.script.codes = ["ok"] * 10
    run(make(stop_after=2), ctx)
    out = capsys.readouterr().out
    assert "A1 creating owned Market A..." in out
    assert "A1 target created: Market A" in out


def test_stage_one_logs_added_failed_and_keep_target(ctx, capsys):
    ctx.script.codes = ["ok", "FLOOD_WAIT_5"]
    config = make(account_labels=["acc1"], stop_after=1)
    assert run(config, ctx) == 1
    out = capsys.readouterr().out
    assert "A1 add contacts: Ann|@ann" in out
    assert "A1 added: Ann|@ann" in out
    assert "A1 keep target: Ann|@ann" in out
    assert "A1 failed: Bob|@bob (FLOOD_WAIT)" in out


def test_no_valid_targets_when_every_group_is_dead(ctx, pattern_file, capsys):
    config = make(pattern=pattern_file(["A4|L0|D6|hello"]),
                  group_titles=["Market A"],
                  activity={"@og_market_a": 96.0}, stop_after=3)
    run(config, ctx)
    assert "No valid targets" in capsys.readouterr().out


def test_group_manager_missing_line(ctx, capsys):
    config = make(group_titles=[], targets=["@existing"], stop_after=2)
    assert run(config, ctx) == 0
    assert "GroupMgr config missing, use defaults." in capsys.readouterr().out


def test_the_invite_line_logs_the_pull(ctx, capsys):
    ctx.script.codes = ["ok"] * 10
    run(make(), ctx)
    assert "[Invite] acc1: 0 own contact(s)" in capsys.readouterr().out


def test_speech_line_names_the_pattern(ctx, pattern_file, capsys):
    ctx.script.codes = ["ok"] * 40
    run(make(pattern=pattern_file(["A4|L0|D6|hello"])), ctx)
    out = capsys.readouterr().out
    assert "[Speech] " in out and "1 line(s) -> 1 group(s)" in out
