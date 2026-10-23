"""Injected answers: parsing, the flood wait split and the classified codes."""

from __future__ import annotations

import pytest

from ogtool.senders import result_for
from ogtool.simulate import (
    ACTIVE,
    FLOOD_WAIT,
    OK,
    PEER_FLOOD,
    RETRYABLE,
    Script,
    describe,
    normalize,
)


def test_normalize_upper_cases_and_defaults_to_ok():
    assert normalize("peer_flood") == "PEER_FLOOD"
    assert normalize("") == OK


def test_describe_splits_the_flood_wait_seconds():
    code, detail, retryable, wait = describe("FLOOD_WAIT_120")
    assert code == FLOOD_WAIT and retryable is True and wait == 120.0
    assert "120" in detail


def test_describe_keeps_a_classified_answer():
    code, _, retryable, wait = describe(ACTIVE)
    assert code == ACTIVE and retryable is False and wait == 0.0


def test_result_for_builds_an_ok_result():
    result = result_for("ok")
    assert result.ok is True and result.code == ""


def test_result_for_marks_peer_flood_retryable():
    result = result_for(PEER_FLOOD)
    assert result.ok is False and result.retryable is True
    assert PEER_FLOOD in RETRYABLE


def test_script_is_consumed_in_order_then_falls_back_to_ok():
    script = Script.parse(["PEER_FLOOD", "FLOOD_WAIT_5"])
    assert [script.next(), script.next(), script.next()] == [
        "PEER_FLOOD", "FLOOD_WAIT_5", "ok",
    ]
    assert script.used == 2


def test_script_accepts_a_single_string():
    assert len(Script.parse("ok")) == 1
    assert len(Script.parse(None)) == 0


@pytest.mark.parametrize("code", ["USER_PRIVACY_RESTRICTED", "CHANNEL_PRIVATE"])
def test_terminal_answers_are_not_retryable(code):
    assert result_for(code).retryable is False
