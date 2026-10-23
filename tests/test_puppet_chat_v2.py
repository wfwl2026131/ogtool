"""#19 话术炒群 v2: staggered starts and the owner-only target list."""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features.puppet_chat_v2 import (
    PuppetV2Config,
    run,
    startup_offsets,
)


def config_for(pattern: str, **overrides) -> PuppetV2Config:
    values = {"targets": ["@own"], "pattern": pattern}
    values.update(overrides)
    return PuppetV2Config(**values)


def test_startup_offsets_stagger_each_account():
    offsets = startup_offsets(["a", "b", "c"], 15)
    assert offsets == {"a": 0.0, "b": 15.0, "c": 30.0}
    assert startup_offsets(["a"], 0) == {"a": 0.0}


def test_from_dict_needs_a_target():
    with pytest.raises(ConfigError):
        PuppetV2Config.from_dict({"puppet_chat_v2": {"pattern": "x"}}, None)  # type: ignore[arg-type]


def test_run_reports_the_started_accounts(ctx, pattern_file, capsys):
    config = config_for(pattern_file(), startup_interval=10)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "started accounts: acc1, acc2" in output
    assert "PLAN: 4 messages" in output


def test_the_second_account_starts_later(ctx, pattern_file, capsys):
    config = config_for(pattern_file(), startup_interval=15)
    run(config, ctx)
    output = capsys.readouterr().out
    # Slot A5 belongs to the second account, so its delay carries the offset.
    assert "A5 account=acc2" in output
    assert "delay=36s" in output


def test_loops_are_announced(ctx, pattern_file, capsys):
    config = config_for(pattern_file(), loops=2)
    run(config, ctx)
    assert "loop 2/2" in capsys.readouterr().out
