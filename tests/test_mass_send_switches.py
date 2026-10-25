"""#5's five switches: start time, load balance, task mark, removal, media dir.

``immediateStart`` / ``scheduledDateTime`` / ``loadBalance`` / ``taskMark`` /
``removeSentCustomTargets`` / ``lastMediaDir`` all come from the reference
page's config, so each one gets a test that would fail if it silently stopped
mattering.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ogtool.content import ContentSpec
from ogtool.features.mass_send import (
    MassSendConfig,
    _apply_task_mark,
    _balanced,
    _remove_sent,
    _resolve_media,
    _start_time,
)
from ogtool.limiter import Budget, Limits
from ogtool.senders import Content
from ogtool.targets import Target


def _targets(count: int) -> list[Target]:
    return [Target(ref=f"@t{index}", kind="group") for index in range(count)]


def test_immediate_start_uses_now():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    start, reason = _start_time(MassSendConfig(), now)
    assert start == now
    assert reason == "immediate"


def test_a_future_schedule_is_honoured():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    config = MassSendConfig(start_mode="scheduled",
                            schedule_at="2026-01-01T13:30:00")
    start, reason = _start_time(config, now)
    assert start == datetime(2026, 1, 1, 13, 30, tzinfo=UTC)
    assert reason == "scheduled"


def test_a_past_schedule_warns_and_starts_now(capsys):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    config = MassSendConfig(start_mode="scheduled",
                            schedule_at="2026-01-01T09:00:00")
    start, reason = _start_time(config, now)
    assert start == now
    assert reason == "scheduled (late)"
    assert "in the past" in capsys.readouterr().out


def test_a_bad_schedule_time_is_reported():
    with pytest.raises(ValueError):
        _start_time(MassSendConfig(start_mode="scheduled",
                                   schedule_at="tomorrow"), datetime.now(UTC))


def test_an_unknown_mode_is_reported():
    with pytest.raises(ValueError):
        _start_time(MassSendConfig(start_mode="whenever"), datetime.now(UTC))


def test_load_balance_prefers_the_account_with_room():
    accounts = ["a1", "a2", "a3"]
    budgets = {
        "a1": Budget(limit=10, used=10),
        "a2": Budget(limit=10, used=5),
        "a3": Budget(limit=10, used=0),
    }
    pairs = _balanced(accounts, _targets(6), Limits(), budgets)
    # a1 has nothing left. Balancing fills the emptiest account first, so a3
    # takes every target until it is level with a2, then a2 gets one: the two
    # end up with the same remaining budget, which is the point of the switch.
    assert [account for account, _ in pairs] == ["a3"] * 5 + ["a2"]


def test_load_balance_stops_when_every_budget_is_spent():
    budgets = {"a1": Budget(limit=1, used=1)}
    assert _balanced(["a1"], _targets(3), Limits(), budgets) == []


def test_round_robin_is_still_the_default():
    config = MassSendConfig()
    assert config.load_balance is False
    assert config.start_mode == "immediate"


def test_the_task_mark_is_appended_once():
    config = MassSendConfig(task_mark="#task-7")
    marked = _apply_task_mark(config, Content(text="hello"))
    assert marked.text == "hello\n#task-7"
    again = _apply_task_mark(config, marked)
    assert again.text == "hello\n#task-7"


def test_the_mark_can_be_switched_off():
    config = MassSendConfig(task_mark="#task-7", task_mark_enabled=False)
    assert _apply_task_mark(config, Content(text="hello")).text == "hello"


def test_no_mark_leaves_a_media_only_message_alone():
    config = MassSendConfig(task_mark="#task-7")
    content = Content(media="/tmp/x.jpg")
    assert _apply_task_mark(config, content).text == ""


def test_removing_sent_targets_rewrites_the_list(tmp_path):
    listing = tmp_path / "custom.txt"
    listing.write_text("@t0\n@t1\n@t2\n", encoding="utf-8")
    config = MassSendConfig(remove_sent_custom_targets=True,
                            custom_targets_file=str(listing))
    planned = _planned_for([Target(ref="@t0", kind="group"),
                            Target(ref="@t2", kind="group")])
    assert _remove_sent(config, planned) == 2
    assert listing.read_text(encoding="utf-8") == "@t1\n"


def test_removal_is_off_by_default(tmp_path):
    listing = tmp_path / "custom.txt"
    listing.write_text("@t0\n", encoding="utf-8")
    config = MassSendConfig(custom_targets_file=str(listing))
    assert _remove_sent(config, _planned_for([Target(ref="@t0", kind="group")])) == 0
    assert listing.read_text(encoding="utf-8") == "@t0\n"


def test_a_missing_custom_file_warns_instead_of_crashing(capsys, tmp_path):
    config = MassSendConfig(remove_sent_custom_targets=True,
                            custom_targets_file=str(tmp_path / "gone.txt"))
    assert _remove_sent(config, []) == 0
    assert "custom target file not found" in capsys.readouterr().out


def test_a_relative_media_path_uses_the_remembered_folder(tmp_path):
    (tmp_path / "pic.jpg").write_bytes(b"")
    config = MassSendConfig(content=ContentSpec(media="pic.jpg"),
                            last_media_dir=str(tmp_path))
    _resolve_media(config)
    assert config.content.media == str(tmp_path / "pic.jpg")


def test_an_absolute_media_path_is_left_alone(tmp_path):
    config = MassSendConfig(content=ContentSpec(media="/tmp/other.jpg"),
                            last_media_dir=str(tmp_path))
    _resolve_media(config)
    assert config.content.media == "/tmp/other.jpg"


def test_a_relative_media_path_without_the_folder_is_left_alone():
    config = MassSendConfig(content=ContentSpec(media="pic.jpg"))
    _resolve_media(config)
    assert config.content.media == "pic.jpg"


def _planned_for(targets: list[Target]):
    from ogtool.features.mass_send import Planned

    return [Planned(index=index + 1, offset=float(index), account="a1",
                    target=target)
            for index, target in enumerate(targets)]


def test_schedule_still_starts_at_the_scheduled_second():
    # The plan's own offsets are relative; a scheduled start simply moves the
    # whole plan, it does not shift the first message to offset zero.
    now = datetime(2026, 5, 5, 8, 0, tzinfo=UTC)
    config = MassSendConfig(start_mode="scheduled",
                            schedule_at="2026-05-05T09:00:00")
    start, _ = _start_time(config, now)
    assert start - now == timedelta(hours=1)
