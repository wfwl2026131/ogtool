"""#12's three input sources, deepScan, the two intervals and the output folder.

``globalInterval`` and ``singleInterval`` are the page's two knobs; the raw and
file sources are ``manualRaw`` / ``sourceFilePath``; ``deepScan`` is the one
that can turn an "invalid" into "restricted", and it only does so when the bound
accounts actually disagree.
"""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features.filter_phone import (
    INPUT_MODES,
    RESTRICTED,
    PhoneConfig,
    read_numbers,
    run,
)


def test_the_three_sources_are_the_pages_three():
    assert INPUT_MODES == ("manual", "raw", "file")


def test_manual_splits_commas():
    numbers, mode = read_numbers({"input_mode": "manual",
                                  "numbers": "+861, +862"})
    assert mode == "manual"
    assert numbers == ["+861", "+862"]


def test_raw_splits_commas_spaces_and_newlines():
    numbers, mode = read_numbers({
        "input_mode": "raw",
        "manual_raw": "+861,+862 +863\n+864;+865",
    })
    assert mode == "raw"
    assert numbers == ["+861", "+862", "+863", "+864", "+865"]


def test_file_wins_when_the_mode_is_not_given(tmp_path):
    listing = tmp_path / "n.txt"
    listing.write_text("+861\n", encoding="utf-8")
    numbers, mode = read_numbers({"source_file": str(listing)})
    assert mode == "file"
    assert numbers == ["+861"]


def test_an_unknown_source_is_a_config_error():
    with pytest.raises(ConfigError):
        read_numbers({"input_mode": "paste"})


def test_a_single_interval_below_the_global_one_is_refused():
    # The per-account gap cannot be tighter than the gap between any two
    # numbers; a pair that says otherwise is a mix-up, not a preference.
    with pytest.raises(ConfigError):
        PhoneConfig.from_dict(
            {"filter_phone": {"numbers": "+861",
                              "single_interval_ms": 100,
                              "global_interval_ms": 500}},
            None,  # type: ignore[arg-type]
        )


def test_equal_or_wider_intervals_are_accepted():
    config = PhoneConfig.from_dict(
        {"filter_phone": {"numbers": "+861",
                          "single_interval_ms": 500,
                          "global_interval_ms": 500}},
        None,  # type: ignore[arg-type]
    )
    assert config.single_interval_ms == 500
    assert config.global_interval_ms == 500


def test_deep_scan_keeps_one_answer(ctx, capsys):
    config = PhoneConfig(numbers=["+861"], deep_scan=False)
    assert run(config, ctx) == 0
    assert "disagrees" not in capsys.readouterr().out


def test_deep_scan_reports_disagreement_as_restricted(ctx, monkeypatch,
                                                      capsys):
    def fake_resolve(account, target):
        from ogtool.senders import SendResult

        # acc1 can see the number, acc2 hits a privacy wall: the two accounts
        # disagree, which is exactly what deepScan is there to surface.
        if account == "acc1":
            return SendResult(ok=True, code="OK", detail="+861")
        return SendResult(ok=False, code="USER_PRIVACY_RESTRICTED")

    monkeypatch.setattr(type(ctx.sender()), "resolve",
                        lambda self, account, target: fake_resolve(account, target),
                        raising=False)
    config = PhoneConfig(numbers=["+861"], deep_scan=True)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "deep scan: +861 disagrees" in output
    assert f"-> {RESTRICTED}" in output


def test_the_output_folder_is_honoured(ctx, capsys, tmp_path):
    config = PhoneConfig(numbers=["+861"], output_dir=str(tmp_path))
    assert run(config, ctx) == 0
    assert (tmp_path / "filter_phone.tsv").is_file()


def test_the_header_names_the_intervals_and_the_mode(ctx, capsys):
    config = PhoneConfig(numbers=["+861"], input_mode="raw",
                         manual_raw="+861",
                         single_interval_ms=0, global_interval_ms=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "input mode: raw" in output
    assert "deep scan: off" in output
    assert "single interval: 0 ms" in output
