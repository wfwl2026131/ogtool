"""#13 filter group: the six buckets, the deep toggle and the six output lists."""

from __future__ import annotations

from pathlib import Path

import pytest

from ogtool.config import ConfigError
from ogtool.features.filter_group import (
    CAN_SEND,
    CHANNEL,
    INVALID,
    MUTED,
    NEEDS_APPROVAL,
    USER,
    GroupConfig,
    classify,
    run,
)
from ogtool.simulate import CHAT_WRITE_FORBIDDEN


def test_classify_maps_every_bucket():
    assert classify("ok", True, deep=False) == CAN_SEND
    assert classify("CAN_SEND", True, deep=False) == CAN_SEND
    assert classify(CHAT_WRITE_FORBIDDEN, False, deep=False) == MUTED
    assert classify("CHANNEL", True, deep=False) == CHANNEL
    assert classify("USER", True, deep=False) == USER
    assert classify("USER_ID_INVALID", False, deep=False) == INVALID


def test_deep_check_decides_the_approval_bucket():
    assert classify("JOIN_REQUEST", True, deep=True) == NEEDS_APPROVAL
    assert classify("JOIN_REQUEST", True, deep=False) == CAN_SEND


def test_run_writes_one_file_per_bucket(ctx, scripted, tmp_path: Path, capsys):
    scripted(["ok", CHAT_WRITE_FORBIDDEN, "CHANNEL", "USER", "USER_ID_INVALID",
              "JOIN_REQUEST"])
    config = GroupConfig(
        groups=["@g1", "@g2", "@g3", "@g4", "@g5", "@g6"],
        deep=True, output_dir=str(tmp_path),
    )
    run(config, ctx)
    out = capsys.readouterr().out
    for name in (CAN_SEND, NEEDS_APPROVAL, MUTED, CHANNEL, INVALID, USER):
        assert f"{name}：1" in out
    assert (tmp_path / "filter_group_can_send.txt").exists()
    assert (tmp_path / "filter_group_needs_approval.txt").read_text().count("@g6") == 1


def test_no_groups_is_a_config_error():
    with pytest.raises(ConfigError):
        GroupConfig.from_dict({}, None)  # type: ignore[arg-type]


def test_deep_off_reports_approval_as_can_send(ctx, scripted, capsys):
    scripted(["JOIN_REQUEST"])
    config = GroupConfig(groups=["@g1"], deep=False)
    run(config, ctx)
    assert f"{CAN_SEND}：1" in capsys.readouterr().out
