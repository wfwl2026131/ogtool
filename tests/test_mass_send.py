"""#5 mass send: planning, the caps, and the dry-run contract."""

from __future__ import annotations

import random
import re

from ogtool.features.mass_send import (
    MassSendConfig,
    _interleave,
    run,
    schedule,
)
from ogtool.limiter import Budget, Limits
from ogtool.targets import parse_many

PLAN_LINE = re.compile(r"^PLAN: (\d+) messages, (\d+) accounts, ETA .+$")


def make_config(**send) -> MassSendConfig:
    raw = {
        "accounts": ["acc1", "acc2"],
        "targets": {"inline": "@a,@b,@c,@d", "kind": "custom"},
        "content": {"text": "hello"},
        "send": {"account_interval_min": 1, "account_interval_max": 2,
                 "group_interval": 0, **send},
        "task_name": "demo",
    }
    return MassSendConfig.from_dict(raw, None, dry_run=True)  # type: ignore[arg-type]


def test_interleave_round_robins_accounts():
    targets = parse_many(["@a", "@b", "@c"])
    pairs = _interleave(["acc1", "acc2"], targets, Limits(), random.Random(0))
    assert [account for account, _ in pairs] == ["acc1", "acc2", "acc1"]


def test_exec_cap_limits_the_plan():
    targets = parse_many(["@a", "@b", "@c", "@d"])
    pairs = _interleave(["acc1"], targets, Limits(exec_cap=2), random.Random(0))
    assert len(pairs) == 2


def test_schedule_spaces_each_account():
    targets = parse_many(["@a", "@b", "@c", "@d"])
    limits = Limits(account_min=10, account_max=10, group_interval=0)
    pairs = _interleave(["acc1", "acc2"], targets, limits, random.Random(0))
    planned = schedule(pairs, limits, accounts=["acc1", "acc2"])
    by_account: dict[str, list[float]] = {}
    for item in planned:
        by_account.setdefault(item.account, []).append(item.offset)
    for offsets in by_account.values():
        gaps = [b - a for a, b in zip(offsets, offsets[1:], strict=False)]
        assert all(gap >= 10 for gap in gaps)


def test_success_cap_stops_the_plan():
    targets = parse_many([f"@t{i}" for i in range(10)])
    limits = Limits(success_cap=3)
    pairs = _interleave(["acc1"], targets, limits, random.Random(0))
    planned = schedule(pairs, limits, accounts=["acc1"])
    assert len(planned) == 3


def test_daily_budget_skips_exhausted_accounts():
    targets = parse_many(["@a", "@b", "@c"])
    limits = Limits(daily_limit=1, account_min=0, account_max=0)
    pairs = _interleave(["acc1", "acc2"], targets, limits, random.Random(0))
    budgets = {"acc1": Budget(1), "acc2": Budget(1)}
    planned = schedule(pairs, limits, accounts=["acc1", "acc2"], budgets=budgets)
    assert len(planned) == 2


def test_dry_run_prints_every_message_and_the_plan(ctx, capsys):
    config = make_config()
    code = run(config, ctx)
    output = capsys.readouterr().out
    assert code == 0

    lines = [line for line in output.splitlines() if line.startswith("[")]
    assert len(lines) == 4
    assert all("account=acc" in line and "target=@" in line for line in lines)
    assert any(line.startswith("DRY-RUN") is False for line in lines)

    plan = [line for line in output.splitlines() if line.startswith("PLAN: ")]
    assert len(plan) == 1
    match = PLAN_LINE.match(plan[0])
    assert match, plan[0]
    assert int(match.group(1)) == 4
    assert int(match.group(2)) == 2
    assert "dry-run: nothing was sent" in output


def test_run_without_targets_fails_cleanly(ctx, capsys):
    config = MassSendConfig.from_dict(
        {"accounts": ["acc1"], "targets": {"inline": ""}, "content": {"text": "x"}},
        None,  # type: ignore[arg-type]
        dry_run=True,
    )
    assert run(config, ctx) == 2
    assert "no targets" in capsys.readouterr().out


def test_results_are_recorded_as_planned(ctx, capsys):
    run(make_config(), ctx)
    rows = ctx.store._conn.execute("SELECT status FROM results").fetchall()
    assert [row["status"] for row in rows] == ["planned"] * 4
    capsys.readouterr()


class OkSender:
    """A live transport that accepts everything, so only the rows are on trial."""

    def __init__(self) -> None:
        self.calls = 0

    def send(self, account: str, target: object, content: object):
        from ogtool.senders import SendResult

        self.calls += 1
        return SendResult(ok=True, message_id=self.calls)

    def close(self) -> None:
        pass


def test_a_live_run_records_sent_rows_and_keeps_its_interval(
        ctx, live, capsys, monkeypatch):
    from ogtool.features import _common

    slept: list[float] = []
    monkeypatch.setattr(_common.time, "sleep",
                        lambda seconds: slept.append(seconds))
    live(OkSender())
    assert run(make_config(), ctx) == 0
    statuses = [row["status"] for row in ctx.store.results_for(ctx.task_id)]
    assert statuses and set(statuses) == {"sent"}, statuses
    assert sum(slept) > 0, "a live run has to keep the interval it planned"


def test_a_dry_run_never_sleeps(ctx, monkeypatch):
    from ogtool.features import _common

    slept: list[float] = []
    monkeypatch.setattr(_common.time, "sleep",
                        lambda seconds: slept.append(seconds))
    assert run(make_config(), ctx) == 0
    assert slept == []
