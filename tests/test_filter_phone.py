"""#12 filter phone: the four buckets, the output file and the counters."""

from __future__ import annotations

from pathlib import Path

import pytest

from ogtool.config import ConfigError
from ogtool.features.filter_phone import (
    ACTIVE,
    INVALID,
    RESTRICTED,
    VALID,
    PhoneConfig,
    classify,
    run,
)
from ogtool.simulate import (
    PEER_FLOOD,
    TIMEOUT,
    USER_PRIVACY_RESTRICTED,
)


def test_classify_sorts_the_answers():
    assert classify("ok", True) == VALID
    assert classify("ACTIVE", True) == ACTIVE
    assert classify(USER_PRIVACY_RESTRICTED, False) == RESTRICTED
    assert classify("USER_ID_INVALID", False) == INVALID


def test_run_fills_all_buckets(ctx, scripted, tmp_path: Path, capsys):
    scripted(["ok", "ACTIVE", USER_PRIVACY_RESTRICTED, "USER_ID_INVALID"])
    output_file = tmp_path / "phones.tsv"
    config = PhoneConfig(numbers=["+15550000001", "+15550000002",
                                  "+15550000003", "+15550000004"],
                         output=str(output_file))
    run(config, ctx)
    out = capsys.readouterr().out
    assert f"{VALID}：1" in out
    assert f"{ACTIVE}：1" in out
    assert f"{RESTRICTED}：1" in out
    assert f"{INVALID}：1" in out
    assert "Processed: 4 / 4" in out
    body = output_file.read_text()
    assert "+15550000001" in body and "有效" in body


def test_numbers_can_come_from_a_file(tmp_path: Path):
    listing = tmp_path / "numbers.txt"
    listing.write_text("+15550000001\n# comment\n+15550000002\n", encoding="utf-8")
    config = PhoneConfig.from_dict(
        {"filter_phone": {"file": str(listing)}}, None)  # type: ignore[arg-type]
    assert config.numbers == ["+15550000001", "+15550000002"]


def test_no_numbers_is_a_config_error():
    with pytest.raises(ConfigError):
        PhoneConfig.from_dict({}, None)  # type: ignore[arg-type]


def test_flood_and_timeout_are_invalid_not_crashes(ctx, scripted, capsys):
    scripted([PEER_FLOOD, TIMEOUT, "FLOOD_WAIT_30"])
    # flood_cap=0 keeps this test about the buckets: the retry path has its own.
    config = PhoneConfig(numbers=["+15550000001", "+15550000002",
                                  "+15550000003"], flood_cap=0)
    run(config, ctx)
    out = capsys.readouterr().out
    assert f"{INVALID}：3" in out
    assert "failed: 3" in out


def test_a_bare_number_is_still_a_phone_on_this_page():
    from ogtool.features.filter_phone import phone_target

    assert phone_target("15550000001").ref == "+15550000001"
    assert phone_target("+15550000001").ref == "+15550000001"
    assert phone_target("15550000001").phone_fallback == ""


def test_flood_cap_retries_a_number_after_a_rest(ctx, scripted, capsys):
    """``floodCap``: 'PEER_FLOOD retries per number'."""
    scripted([PEER_FLOOD, "ok"])
    config = PhoneConfig(numbers=["+15550000001"], flood_cap=1,
                         single_interval_ms=0, global_interval_ms=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "PEER_FLOOD backoff" in output
    assert f"{VALID}：1" in output


def test_threads_split_the_numbers_over_accounts(ctx, scripted, capsys):
    scripted(["ok", "ok", "ok", "ok"])
    config = PhoneConfig(numbers=[f"+1555000000{i}" for i in range(4)],
                         threads=2, single_interval_ms=0,
                         global_interval_ms=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "threads: 2 lanes, one account each" in output
    assert f"{VALID}：4" in output
