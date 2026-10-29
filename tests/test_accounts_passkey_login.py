"""The batch_login page's passkey branch, read side.

The reference's own lines: the Start-gate classifier (:1737360-1737363), the
mixed refusal (:1742499), the empty-selection refusal (:1742494), the missing
file (:1742500), the task body's file reader (:1737357-1737374), the build's
own replay boundary (:1737375-1737377), and the closing count (:1737378). The
reader validates exactly what the reference's own reader validates, and a
valid record still blocks - secure replay is not implemented in this build.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ogtool.accounts import _write_passkey, passkey_login


def _passkey(folder: Path, name: str, payload: dict | str) -> Path:
    path = folder / name
    text = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def folder(tmp_path: Path) -> Path:
    place = tmp_path / "in"
    place.mkdir()
    return place


def test_a_valid_record_reads_and_blocks_live(folder: Path):
    good = _passkey(
        folder,
        "good.passkey",
        {"version": 1, "kind": "metadata", "PhoneNumber": "+8613800000001"},
    )
    rows = passkey_login([good], live=True)
    assert rows == [
        (
            "good",
            False,
            "Passkey login blocked for good.passkey: "
            "secure replay is not implemented in this build.",
        )
    ]


def test_a_dry_run_plans_a_blocked_replay_row(folder: Path):
    good = _passkey(folder, "good.passkey", {"PhoneNumber": "+8613800000001"})
    rows = passkey_login([good], live=False)
    assert rows == [
        (
            "good",
            True,
            "dry-run: would attempt passkey login; replay is blocked in this build",
        )
    ]


def test_a_missing_phone_is_the_page_s_own_line(folder: Path):
    bare = _passkey(folder, "bare.passkey", {"kind": "metadata"})
    rows = passkey_login([bare], live=True)
    assert rows == [("bare", False, f"Passkey file missing PhoneNumber: {bare}")]


def test_broken_json_is_the_page_s_own_line(folder: Path):
    junk = _passkey(folder, "junk.passkey", "not json at all")
    rows = passkey_login([junk], live=True)
    assert rows == [("junk", False, f"Invalid passkey file format: {junk}")]


def test_a_missing_file_is_a_failure_row_not_a_refusal(folder: Path):
    ghost = folder / "ghost.passkey"
    rows = passkey_login([ghost], live=True)
    assert rows == [("ghost", False, f"Failed to open passkey file: {ghost}")]


def test_the_export_feeds_the_login_reader_unchanged(folder: Path, tmp_path: Path):
    out = tmp_path / "out"
    out.mkdir()
    _write_passkey(out, "acc1", "+8613800000001")
    rows = passkey_login(sorted(out.glob("*.passkey")), live=False)
    assert len(rows) == 1
    # The stem of the export name ``passkey%1_%2`` (:1737371) is the timestamp,
    # not the label - the row is labelled by stem either way.
    assert rows[0][1] is True
