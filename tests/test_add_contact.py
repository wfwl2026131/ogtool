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
