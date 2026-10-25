"""The page's schedule and preview switches on #0 and #19.

``scheduleDateTime`` holds the run until a wall clock time; ``disableLinkPreview``
turns the link card off on the lines that carry a URL. Both are reported in the
header, because a run that starts at a different time than the page says is a
run nobody can trust.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ogtool.config import ConfigError
from ogtool.features.puppet_chat import PuppetConfig, schedule_wait
from ogtool.features.puppet_chat_v2 import PuppetV2Config, startup_offsets
from ogtool.senders import Content


def test_no_schedule_means_now():
    wait, when = schedule_wait(PuppetConfig(), datetime(2026, 1, 1, tzinfo=UTC))
    assert wait == 0.0
    assert when == "now"


def test_a_future_schedule_is_waited_out():
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    config = PuppetConfig(schedule_at="2026-01-01T10:30:00")
    wait, when = schedule_wait(config, now)
    assert wait == 1800.0
    assert when == "2026-01-01T10:30:00"


def test_a_past_schedule_is_named_and_not_waited():
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    config = PuppetConfig(schedule_at="2026-01-01T09:00:00")
    wait, when = schedule_wait(config, now)
    assert wait == 0.0
    assert "past" in when


def test_a_bad_schedule_is_a_config_error():
    with pytest.raises(ConfigError):
        schedule_wait(PuppetConfig(schedule_at="tonight"),
                      datetime.now(UTC))


def test_the_preview_switch_defaults_to_on():
    assert PuppetConfig().disable_link_preview is False
    assert PuppetV2Config().disable_link_preview is False


def test_the_content_carries_the_preview_switch():
    plain = Content(text="see https://example.com")
    off = Content(text="see https://example.com", no_preview=True)
    assert plain.no_preview is False
    assert off.no_preview is True


def test_v2_reads_the_schedule_and_preview(tmp_path, settings):
    script = tmp_path / "s.txt"
    script.write_text("A4|L0|D6|hello\n", encoding="utf-8")
    config = PuppetV2Config.from_dict(
        {"puppet_chat_v2": {
            "targets": "@group",
            "pattern": str(script),
            "schedule_at": "2026-01-01T10:00:00",
            "disable_link_preview": True,
        }},
        settings,
    )
    assert config.schedule_at == "2026-01-01T10:00:00"
    assert config.disable_link_preview is True


def test_the_startup_interval_still_staggers_the_accounts():
    offsets = startup_offsets(["a", "b", "c"], 4.0)
    assert offsets == {"a": 0.0, "b": 4.0, "c": 8.0}


def test_the_schedule_is_a_delay_not_a_time_of_day():
    # A scheduled run waits the difference; it does not sleep until some
    # absolute moment the clock may never reach (past times wait zero).
    now = datetime(2026, 6, 1, 23, 0, tzinfo=UTC)
    config = PuppetConfig(schedule_at="2026-06-02T00:30:00")
    wait, _ = schedule_wait(config, now)
    assert wait == timedelta(hours=1, minutes=30).total_seconds()
