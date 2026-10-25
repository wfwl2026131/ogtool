"""#16 add contact: the per-target cap, the caps and the failure counters."""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features.add_contact import ContactConfig, plan_jobs, run


def test_per_target_cap_repeats_the_contact():
    jobs = plan_jobs(["@c1", "@c2"], ["a"], per_target_cap=2)
    assert [(contact, round_index) for _, contact, round_index in jobs] == [
        ("@c1", 1), ("@c1", 2), ("@c2", 1), ("@c2", 2),
    ]


def test_exec_cap_trims_the_queue():
    assert len(plan_jobs([f"@c{i}" for i in range(5)], ["a"], exec_cap=3)) == 3


def test_success_cap_trims_the_queue():
    assert len(plan_jobs([f"@c{i}" for i in range(5)], ["a"],
                         success_cap=2)) == 2


def test_run_reports_contacts_and_plan(ctx, capsys):
    config = ContactConfig(contacts=["@c1", "@c2"], per_target_cap=1)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "联系人：2" in output
    assert "PLAN: 2 contacts" in output


def test_privacy_restriction_is_counted(ctx, scripted, capsys):
    scripted(["USER_PRIVACY_RESTRICTED"])
    config = ContactConfig(contacts=["@c1"], per_target_cap=1)
    assert run(config, ctx) == 1
    output = capsys.readouterr().out
    assert "USER_PRIVACY_RESTRICTED" in output
    assert "失败: 1" in output


def test_no_contacts_is_a_config_error():
    with pytest.raises(ConfigError):
        ContactConfig.from_dict({}, None)  # type: ignore[arg-type]


def test_duplicates_in_the_contact_list_are_collapsed():
    config = ContactConfig.from_dict(
        {"add_contact": {"contacts": "@c1,@c1,@c2"}}, None)  # type: ignore[arg-type]
    assert config.contacts == ["@c1", "@c2"]


def test_a_live_run_waits_the_request_gap_between_two_requests(
        ctx, live, ok_sender, slept, capsys):
    live(ok_sender)
    config = ContactConfig(contacts=["@c1", "@c2", "@c3"], per_target_cap=1,
                           request_interval_ms=1000,
                           operation_interval_min_ms=500,
                           operation_interval_max_ms=500)
    assert run(config, ctx) == 0
    # Three requests, so two gaps; each is the request interval plus the
    # operation interval (1s + 0.5s).
    assert slept == [1.5, 1.5]
    assert "waited: " in capsys.readouterr().out


def test_a_dry_run_never_sleeps(ctx, slept, capsys):
    config = ContactConfig(contacts=["@c1", "@c2", "@c3"], per_target_cap=1,
                           request_interval_ms=1000)
    run(config, ctx)
    assert slept == []
    assert "waited: " not in capsys.readouterr().out


class LaneSender:
    """Records which account added whom, and when."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []

    def add_contact(self, account: str, target: object):
        import time as clock

        from ogtool.senders import SendResult

        self.calls.append((account, getattr(target, "ref", "?"),
                           clock.monotonic()))
        return SendResult(ok=True)

    def close(self) -> None:
        pass


def test_two_accounts_add_at_the_same_time(ctx, live):

    sender = LaneSender()
    live(sender)
    config = ContactConfig(contacts=["@c1", "@c2", "@c3", "@c4"],
                           account_labels=["acc1", "acc2"], threads=2,
                           request_interval_ms=0,
                           operation_interval_min_ms=100,
                           operation_interval_max_ms=100)
    assert run(config, ctx) == 0
    seen: dict[str, list[float]] = {}
    for account, _, at in sender.calls:
        seen.setdefault(account, []).append(at)
    assert set(seen) == {"acc1", "acc2"}
    assert min(seen["acc2"]) < max(seen["acc1"])


def test_one_account_is_never_worked_by_two_lanes(ctx, live):
    sender = LaneSender()
    live(sender)
    config = ContactConfig(contacts=["@c1", "@c2", "@c3"],
                           account_labels=["acc1"], threads=8,
                           request_interval_ms=0,
                           operation_interval_min_ms=0,
                           operation_interval_max_ms=0)
    assert run(config, ctx) == 0
    assert {account for account, _, _ in sender.calls} == {"acc1"}
    assert len(sender.calls) == 3


def test_flood_cap_retries_the_contact_after_a_rest(ctx, scripted, capsys,
                                                    slept):
    """``floodCap`` is 'PEER_FLOOD retries per contact', not a give-up count."""
    scripted(["PEER_FLOOD", "ok"])
    config = ContactConfig(contacts=["@c1"], flood_cap=1,
                           operation_interval_min_ms=0,
                           operation_interval_max_ms=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "PEER_FLOOD backoff" in output
    assert "成功: 1" in output


def test_a_contact_that_stays_flooded_is_counted_after_the_cap(ctx, scripted,
                                                              capsys):
    scripted(["PEER_FLOOD", "PEER_FLOOD", "PEER_FLOOD"])
    config = ContactConfig(contacts=["@c1"], flood_cap=1,
                           operation_interval_min_ms=0,
                           operation_interval_max_ms=0)
    assert run(config, ctx) == 1
    assert "失败: 1" in capsys.readouterr().out
