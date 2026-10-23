"""Pacing: intervals, daily budgets and the PEER_FLOOD backoff."""

from __future__ import annotations

import random

from ogtool.limiter import (
    Budget,
    Limits,
    Pacer,
    PeerFloodBackoff,
    format_duration,
)


def test_limits_fix_inverted_interval():
    limits = Limits(account_min=10, account_max=4, threads=0)
    assert limits.account_max == 10
    assert limits.threads == 1


def test_budget_stops_at_the_daily_limit():
    budget = Budget(limit=2)
    assert budget.take() and budget.take()
    assert not budget.take()
    assert budget.left == 0


def test_budget_zero_limit_is_unlimited():
    budget = Budget(limit=0)
    assert all(budget.take() for _ in range(5))
    assert budget.used == 5


def test_backoff_grows_and_caps():
    backoff = PeerFloodBackoff(base=10, factor=2, cap=50, jitter=0.0)
    assert backoff.wait_for(0) == 10
    assert backoff.wait_for(1) == 20
    assert backoff.wait_for(2) == 40
    assert backoff.wait_for(5) == 50


def test_backoff_keeps_jitter_inside_the_cap():
    backoff = PeerFloodBackoff(base=10, factor=3, cap=100, jitter=0.5)
    for attempt in range(6):
        wait = backoff.wait_for(attempt)
        assert 1.0 <= wait <= 100


def test_pacer_interval_inside_the_configured_range():
    pacer = Pacer(Limits(account_min=5, account_max=6, group_interval=0),
                  rng=random.Random(1))
    for _ in range(20):
        delay = pacer.delay_for("acc", "target")
        assert 5.0 <= delay <= 6.0


def test_pacer_respects_the_group_interval():
    pacer = Pacer(Limits(account_min=0, account_max=0, group_interval=30),
                  rng=random.Random(1))
    pacer.delay_for("acc", "@group")
    pacer.mark("acc", "@group")
    pacer.advance(10)
    assert pacer.delay_for("acc", "@group") == 20


def test_eta_scales_with_threads():
    one = Pacer(Limits(account_min=10, account_max=10, threads=1)).eta(10)
    two = Pacer(Limits(account_min=10, account_max=10, threads=2)).eta(10)
    assert one == 100
    assert two == 50


def test_format_duration():
    assert format_duration(0) == "0s"
    assert format_duration(65) == "1m05s"
    assert format_duration(3750) == "1h02m30s"
