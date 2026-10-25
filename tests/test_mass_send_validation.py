"""The page's own configuration rules for mass send (dump 1741448-1453).

Two are refusals and the rest are warnings the page still starts with - which is
why they are two lists and not one:
  "Per-account success limit must be between 0 and 100000"
  "Per-account attempt limit must be between 0 and 100000"
  "Per-account attempt limit must be greater than or equal to success limit"
  "Per-account limit (%1) is higher than total targets (%2), limit will not be effective"
  "Load balancing is enabled but only one account is selected"
  "Daily limit implies it may take multiple days: capacityPerDay=%1 targets, totalTargets=%2"
"""

from __future__ import annotations

from ogtool.features.mass_send import LIMIT_CEILING, MassSendConfig, validate
from ogtool.limiter import Limits


def _config(**limits) -> MassSendConfig:
    return MassSendConfig(limits=Limits(**limits))


def test_the_ceiling_is_the_page_s_own_number():
    assert LIMIT_CEILING == 100_000


def test_a_cap_above_the_ceiling_is_an_error():
    errors, _ = validate(_config(exec_cap=100_001), ["a"], [object()])
    assert errors and "between 0 and 100000" in errors[0]


def test_a_negative_cap_is_an_error():
    errors, _ = validate(_config(success_cap=-1), ["a"], [object()])
    assert errors and "success" in errors[0]


def test_attempt_below_success_is_an_error():
    errors, _ = validate(_config(success_cap=50, exec_cap=10), ["a"],
                         [object()] * 100)
    assert any("greater than or equal" in line for line in errors)


def test_a_limit_above_the_target_count_only_warns():
    errors, warnings = validate(_config(success_cap=99), ["a"], [object()] * 3)
    assert errors == []
    assert "will not be effective" in warnings[0]
    assert "(99)" in warnings[0] and "(3)" in warnings[0]


def test_load_balance_with_one_account_only_warns():
    _, warnings = validate(
        MassSendConfig(limits=Limits(), load_balance=True), ["a"], [object()])
    assert any("only one account" in line for line in warnings)


def test_load_balance_with_two_accounts_says_nothing():
    _, warnings = validate(
        MassSendConfig(limits=Limits(), load_balance=True), ["a", "b"],
        [object()])
    assert not any("load balancing" in line.lower() for line in warnings)


def test_a_daily_limit_below_the_workload_estimates_the_days():
    _, warnings = validate(_config(daily_limit=2), ["a", "b"], [object()] * 8)
    assert any("capacityPerDay=4" in line and "totalTargets=8" in line
               for line in warnings)


def test_a_daily_limit_that_covers_the_workload_says_nothing():
    _, warnings = validate(_config(daily_limit=50), ["a", "b"], [object()] * 10)
    assert warnings == []


def test_the_limits_are_reported_verbatim():
    errors, warnings = validate(_config(success_cap=99, daily_limit=1),
                                ["a"], [object()] * 3)
    assert errors == []
    joined = " | ".join(warnings)
    assert "Per-account limit (99)" in joined
    assert "Daily limit implies it may take multiple days" in joined
