"""#20 mix market: the five stages, stop-after, dead chats and the rest."""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features import mix_market
from ogtool.features.mix_market import (
    STAGES,
    MixConfig,
    create_refs,
    dead_chats,
    rest_after_failure,
    run,
    slug,
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


def test_titles_become_parseable_refs():
    assert slug("Market Group A") == "@og_market_group_a"
    assert create_refs(MixConfig(group_titles=["Market A", "Market B"])) == [
        "@og_market_a", "@og_market_b",
    ]


def test_dead_chats_uses_the_threshold():
    config = MixConfig(group_titles=["A"], dead_after_hours=72,
                       activity={"@og_a": 96.0, "@og_b": 12.0})
    assert dead_chats(config, ["@og_a", "@og_b"]) == ["@og_a"]


def test_the_rest_grows_with_the_failures_and_is_capped():
    config = MixConfig(group_titles=["A"], create_rest_min=1.0,
                       create_rest_max=5.0)
    assert rest_after_failure(config, 0) == 1.0
    assert rest_after_failure(config, 1) == pytest.approx(1 + 4 / 3)
    assert rest_after_failure(config, 9) == 5.0


def test_all_five_stages_run_end_to_end(ctx, pattern_file, capsys):
    ctx.script.codes = ["ok"] * 40
    config = make(pattern=pattern_file(["A4|L0|D6|hello there"]))
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert f"stage 1/{len(STAGES)} add_contact" in output
    assert f"stage 2/{len(STAGES)} create_group" in output
    assert f"stage 3/{len(STAGES)} puppet_chat_v2" in output
    assert f"stage 4/{len(STAGES)} batch_pull" in output
    assert f"stage 5/{len(STAGES)} mass_send_v2" in output
    assert "Processed: 5 / 5" in output


def test_every_stage_leaves_an_audit_row(ctx, pattern_file, capsys):
    # Stages 4 and 5 used to bump their own counters and record nothing, so a
    # run that had pulled and messaged looked like it had done neither.
    ctx.script.codes = ["ok"] * 40
    config = make(pattern=pattern_file(["A4|L0|D6|hello there"]))
    assert run(config, ctx) == 0
    # A nested feature (stage 3) opens its own task and moves ctx.task_id, so
    # the assertion reads the whole page, not just the last task it opened.
    rows = ctx.store.results_for_feature("mix_market")
    targets = {row["target"] for row in rows}
    assert "Ann|@ann" not in targets
    # Stage 4 is the pull, and it records one row per account it read.
    assert "own contacts" in targets, f"no pull row in {targets}"
    assert targets & {"@ann", "@bob"}, f"no send row in {targets}"


def test_the_created_groups_are_what_the_later_stages_use(ctx, pattern_file,
                                                          capsys):
    create_refs_ = create_refs(make(group_titles=["Market A"]))
    config = make(pattern=pattern_file(["A4|L0|D6|hello there"]),
                  group_titles=["Market A"])
    run(config, ctx)
    output = capsys.readouterr().out
    ref = create_refs_[0]
    assert f"target={ref} hours=unknown -> alive" in output
    # The last stage messages the contacts the pull stage collected.
    assert "pull: nothing returned, the configured contacts stand in" in output
    assert "stage 5/5 mass_send_v2: ok=2" in output


def test_stop_after_cuts_the_run(ctx, pattern_file, capsys):
    config = make(pattern=pattern_file(["A4|L0|D6|hello"]), stop_after=2)
    run(config, ctx)
    output = capsys.readouterr().out
    assert "stage 2/5 create_group" in output
    assert "stage 3/5" not in output
    assert "Processed: 2 / 2" in output


def test_a_dead_chat_is_reported_and_dropped(ctx, pattern_file, capsys):
    config = make(pattern=pattern_file(["A4|L0|D6|hello"]),
                  group_titles=["Market A", "Market B"],
                  activity={"@og_market_b": 96.0})
    run(config, ctx)
    output = capsys.readouterr().out
    assert "target=@og_market_b hours=96 -> dead" in output
    assert "dead chats: 1" in output
    # Stage 3 only played into the group that is still alive.
    assert output.count("target=@og_market_b content=") == 0


def test_a_failed_create_prints_the_rest(ctx, pattern_file, capsys):
    # The contacts pass, then both creates fail and the rest grows.
    ctx.script.codes = ["ok", "ok", "FLOOD_WAIT_30", "FLOOD_WAIT_30"]
    config = make(pattern=pattern_file(["A4|L0|D6|hello"]),
                  group_titles=["Market A", "Market B"])
    run(config, ctx)
    output = capsys.readouterr().out
    assert "rest: create failed at 'Market A'" in output
    assert "waiting 2 min" in output
    assert "rest: create failed at 'Market B'" in output
    assert "waiting 4 min" in output


def test_nothing_to_work_on_is_a_config_error():
    with pytest.raises(ConfigError):
        MixConfig.from_dict({"mix_market": {"pattern": "x"}},
                            None)  # type: ignore[arg-type]


def test_a_bad_activity_entry_is_a_config_error():
    with pytest.raises(ConfigError):
        MixConfig.from_dict(
            {"mix_market": {"group_title": ["A"], "activity": ["@a"]}},
            None,  # type: ignore[arg-type]
        )


def test_the_stage_names_are_the_documented_order():
    # The reference's own numbering: contacts, owned group, puppet chat, PULL,
    # then DM. Stage 4 is a pull - it reads the address book, it invites nobody.
    assert [name for name, _label in STAGES] == [
        "add_contact", "create_group", "puppet_chat_v2",
        "batch_pull", "mass_send_v2",
    ]
    assert mix_market.STAGES[0][1] == "add contacts"
    assert mix_market.STAGES[3][1] == "pull own contacts"
