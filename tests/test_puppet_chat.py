"""#0 话术炒群: script replay, slot mapping, mode file and the daily limit."""

from __future__ import annotations

from pathlib import Path

import pytest

from ogtool.chat_pattern import PatternError
from ogtool.config import ConfigError
from ogtool.features.puppet_chat import (
    PuppetConfig,
    parse_modes,
    resolve_pattern,
    run,
)


def config_for(pattern: str, **overrides) -> PuppetConfig:
    values = {"targets": ["@g1"], "pattern": pattern}
    values.update(overrides)
    return PuppetConfig(**values)


def test_from_dict_needs_a_target():
    with pytest.raises(ConfigError):
        PuppetConfig.from_dict({"puppet_chat": {"pattern": "x.txt"}}, None)  # type: ignore[arg-type]


def test_from_dict_needs_a_script():
    with pytest.raises(ConfigError):
        PuppetConfig.from_dict({"puppet_chat": {"targets": "@g1"}}, None)  # type: ignore[arg-type]


def test_mode_file_resolves_a_named_script(tmp_path: Path):
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("A4|L0|D1|x\n", encoding="utf-8")
    second.write_text("A4|L0|D1|y\n", encoding="utf-8")
    modes = tmp_path / "modes.txt"
    modes.write_text(f"intro={first}\nmain={second}\n", encoding="utf-8")

    assert parse_modes(modes) == [("intro", str(first)), ("main", str(second))]
    picked = resolve_pattern("", str(modes), "main", tmp_path)
    assert picked == second
    with pytest.raises(ConfigError):
        resolve_pattern("", str(modes), "nope", tmp_path)


def test_a_missing_script_is_a_pattern_error(tmp_path: Path):
    with pytest.raises(PatternError):
        resolve_pattern(str(tmp_path / "gone.txt"), "", "", tmp_path)


def test_run_replays_every_line(ctx, pattern_file, capsys):
    config = config_for(pattern_file())
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "[001/004]" in output
    assert "[004/004]" in output
    assert "PLAN: 4 messages, 2 accounts" in output


def test_multiplier_multiplies_the_plan(ctx, pattern_file, capsys):
    config = config_for(pattern_file(), multiplier=2)
    run(config, ctx)
    assert "PLAN: 8 messages" in capsys.readouterr().out


def test_daily_limit_rebinds_a_spent_slot(ctx, pattern_file, capsys):
    # Both lines use slot A4, so the second one is over the limit and has to
    # move to the other account.
    path = pattern_file(["A4|L0|D1|one", "A4|L0|D1|two"])
    config = config_for(path, daily_limit=1)
    run(config, ctx)
    output = capsys.readouterr().out
    assert "daily limit: account=acc1 count=1 limit=1" in output
    assert "reassign puppet A4 -> acc2" in output
    assert "skipped: 0" in output


def test_daily_limit_skips_when_no_account_is_free(ctx, pattern_file, capsys):
    config = config_for(pattern_file(), daily_limit=1)
    run(config, ctx)
    output = capsys.readouterr().out
    assert "skipped: no account under the daily limit" in output
    assert "skipped: 2" in output


def test_loops_repeat_the_script(ctx, pattern_file, capsys):
    config = config_for(pattern_file(), loops=2)
    run(config, ctx)
    output = capsys.readouterr().out
    assert "loop 2/2" in output
    assert "PLAN: 8 messages" in output
