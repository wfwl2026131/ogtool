"""#16's three input modes and its existing-contact count.

``contactsInputMode`` decides where the list comes from; ``countExistingContacts``
decides whether the run knows what every account already had. A failed count is
reported as unknown rather than as zero, because "added 40" means something
different on an account that started at 800.
"""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features.add_contact import (
    INPUT_MODES,
    ContactConfig,
    _count_existing,
    read_contacts,
    run,
)


def test_the_three_modes_are_the_pages_three():
    assert INPUT_MODES == ("manual", "text", "file")


def test_manual_mode_reads_the_typed_list():
    contacts, mode = read_contacts({"input_mode": "manual",
                                    "contacts": "@a, @b"})
    assert mode == "manual"
    assert contacts == ["@a", "@b"]


def test_text_mode_splits_the_blob():
    contacts, mode = read_contacts({
        "input_mode": "text",
        "input_text": "@a\n\n@b\n",
    })
    assert mode == "text"
    assert contacts == ["@a", "@b"]


def test_file_mode_reads_every_file(tmp_path):
    one = tmp_path / "one.txt"
    one.write_text("@a\n", encoding="utf-8")
    two = tmp_path / "two.txt"
    two.write_text("@b\n", encoding="utf-8")
    contacts, mode = read_contacts({"input_mode": "file",
                                    "files": [str(one), str(two)]})
    assert mode == "file"
    assert contacts == ["@a", "@b"]


def test_the_mode_is_inferred_when_it_is_not_given(tmp_path):
    listing = tmp_path / "c.txt"
    listing.write_text("@a\n", encoding="utf-8")
    assert read_contacts({"files": [str(listing)]})[1] == "file"
    assert read_contacts({"input_text": "@a\n"})[1] == "text"
    assert read_contacts({"contacts": "@a"})[1] == "manual"


def test_an_unknown_mode_is_a_config_error():
    with pytest.raises(ConfigError):
        read_contacts({"input_mode": "clipboard"})


def test_a_config_carries_the_mode_and_the_switch(tmp_path):
    listing = tmp_path / "c.txt"
    listing.write_text("@a\n", encoding="utf-8")
    config = ContactConfig.from_dict(
        {"add_contact": {"files": [str(listing)], "count_existing": True}},
        None,  # type: ignore[arg-type]
    )
    assert config.input_mode == "file"
    assert config.count_existing is True
    assert config.contacts == ["@a"]


def test_the_count_is_printed_per_account(ctx, capsys):
    config = ContactConfig(contacts=["@a"])
    counts = _count_existing(config, ["acc1", "acc2"], ctx)
    output = capsys.readouterr().out
    assert "existing contacts: acc1=" in output
    assert "existing contacts total:" in output
    assert isinstance(counts, dict)


def test_a_counting_run_reports_the_mode(ctx, capsys):
    config = ContactConfig(contacts=["@a"], count_existing=True,
                           request_interval_ms=0,
                           operation_interval_min_ms=0,
                           operation_interval_max_ms=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "input mode: manual" in output
    assert "existing contacts" in output
