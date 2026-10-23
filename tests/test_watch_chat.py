"""#7 watch chat: the exclusion list, the relay and the replay."""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features.watch_chat import (
    Incoming,
    WatchConfig,
    excluded,
    parse_incoming,
    run,
)
from ogtool.targets import parse_many


def test_excluded_returns_the_matching_keyword():
    assert excluded("Buy cheap PRICE now", ["spam", "price"]) == "price"
    assert excluded("hello", ["spam"]) == ""


def test_parse_incoming_accepts_pipe_rows_and_bare_text():
    rows = parse_incoming(["@peer|@sender|hello", "just text"])
    assert rows[0] == Incoming("@peer", "@sender", "hello")
    assert rows[1] == Incoming("", "", "just text")


def test_relay_and_skip_are_both_printed(ctx, capsys):
    config = WatchConfig(
        targets=parse_many(["@watch1"], "custom"),
        exclude=["spam"],
        relay="@relay",
        incoming=[Incoming("@watch1", "@a", "hello"),
                  Incoming("@watch1", "@b", "spam here")],
    )
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "relayed to @relay" in output
    assert "SKIP excluded by 'spam'" in output
    assert "relayed: 1  dropped: 1" in output


def test_no_relay_group_keeps_everything_out(ctx, capsys):
    config = WatchConfig(
        targets=parse_many(["@watch1"], "custom"),
        incoming=[Incoming("@watch1", "@a", "hello")],
    )
    run(config, ctx)
    assert "SKIP no relay group" in capsys.readouterr().out


def test_no_target_is_a_config_error():
    with pytest.raises(ConfigError):
        WatchConfig.from_dict({}, None)  # type: ignore[arg-type]
