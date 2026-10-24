"""#18 mass send v2: per account contacts, cross account dedupe, resume."""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features.mass_send_v2 import (
    Contact,
    MassV2Config,
    dedupe_contacts,
    distribute_targets,
    parse_contacts,
    run,
    schedule,
)


def book(*entries: tuple[str, str]) -> dict[str, list[Contact]]:
    out: dict[str, list[Contact]] = {}
    for account, ref in entries:
        out.setdefault(account, []).append(Contact(account, ref, ref.strip("@")))
    return out


def test_contacts_come_from_each_accounts_own_book():
    flat, dropped = dedupe_contacts(
        book(("a1", "@ann"), ("a1", "@bob"), ("a2", "@cid")), False)
    assert [item.ref for item in flat] == ["@ann", "@bob", "@cid"]
    assert dropped == 0


def test_the_same_contact_is_dropped_unless_duplicates_are_allowed():
    flat, dropped = dedupe_contacts(
        book(("a1", "@ann"), ("a2", "@ann")), False)
    assert [item.ref for item in flat] == ["@ann"]
    assert dropped == 1

    kept, none = dedupe_contacts(book(("a1", "@ann"), ("a2", "@ann")), True)
    assert len(kept) == 2
    assert none == 0


def test_schedule_respects_caps_and_budgets():
    flat, _ = dedupe_contacts(
        book(("a1", "@ann"), ("a1", "@bob"), ("a1", "@cid")), False)
    config = MassV2Config(text="hi", success_cap=2)
    plan = schedule(flat, config)
    assert len(plan.pairs) == 2
    assert plan.pairs[0].offset == 0.0
    assert plan.pairs[1].offset >= config.account_interval_min


def test_a_rerun_skips_what_the_results_table_already_holds(ctx, capsys):
    config = MassV2Config(text="hi", account_labels=["acc1"],
                          simulated={"acc1": [Contact("acc1", "@ann", "Ann")]})
    assert run(config, ctx) == 0
    first = capsys.readouterr().out
    assert "queue: 1" in first

    # Mark it delivered the way a live run would, then rerun.
    earlier = ctx.store.create_task("earlier", "mass_send_v2")
    ctx.store.add_result(earlier.id, "acc1", "@ann", "sent")

    assert run(config, ctx) == 0
    second = capsys.readouterr().out
    assert "resumed: 1" in second
    assert "queue: 0" in second


def test_parse_contacts_reads_both_spellings():
    from_cli = parse_contacts(["acc1|Ann|@ann", "acc2|@bob"], {})
    assert [item.ref for item in from_cli["acc1"]] == ["@ann"]
    assert [item.ref for item in from_cli["acc2"]] == ["@bob"]

    from_yaml = parse_contacts(None, {"contacts": {"acc1": ["Cid|@cid"]}})
    assert [item.ref for item in from_yaml["acc1"]] == ["@cid"]


def test_a_missing_message_is_a_config_error():
    with pytest.raises(ConfigError):
        MassV2Config.from_dict({"mass_send_v2": {"text": ""}},
                               None)  # type: ignore[arg-type]


def test_daily_limit_skips_the_overflow(ctx, capsys):
    config = MassV2Config(
        text="hi", account_labels=["acc1"], daily_limit=1,
        simulated={"acc1": [Contact("acc1", "@ann", "Ann"),
                            Contact("acc1", "@bob", "Bob")]},
    )
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "over budget: 1" in output
    assert "queue: 1" in output


def test_an_explicit_target_list_replaces_the_contact_books():
    """`--targets` has to reach the plan without asking Telegram anything."""

    config = MassV2Config(text="hi", targets=["@a", "@b", "@c"])
    book = distribute_targets(config.targets, ["acc1", "acc2"], False)
    flat, dropped = dedupe_contacts(book, config.allow_duplicates)
    assert sorted(contact.ref for contact in flat) == ["@a", "@b", "@c"]
    assert dropped == 0
    assert sorted(contact.account for contact in flat) == ["acc1", "acc1", "acc2"]


def test_a_target_list_round_robins_the_accounts():
    book = distribute_targets(["@a", "@b", "@c"], ["acc1", "acc2"], False)
    assert [c.ref for c in book["acc1"]] == ["@a", "@c"]
    assert [c.ref for c in book["acc2"]] == ["@b"]


def test_duplicates_send_every_target_from_every_account():
    book = distribute_targets(["@a"], ["acc1", "acc2"], True)
    assert [c.ref for c in book["acc1"]] == ["@a"]
    assert [c.ref for c in book["acc2"]] == ["@a"]


def test_a_target_list_with_no_account_plans_nothing():
    assert distribute_targets(["@a"], [], False) == {}
