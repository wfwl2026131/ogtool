"""#21 keep online: the heartbeat schedule and the dry-run contract."""

from __future__ import annotations

from ogtool.features.keep_online import KeepOnlineConfig, beats, run


def test_beats_spread_accounts_inside_the_interval():
    config = KeepOnlineConfig(interval=100, loops=2, jitter=0.1)
    schedule = beats(config, ["acc1", "acc2", "acc3"])
    assert len(schedule) == 6
    assert schedule[0] == (1, 0.0, "acc1")
    assert schedule[1] == (1, 10.0, "acc2")
    assert schedule[2] == (1, 20.0, "acc3")
    assert schedule[3] == (2, 100.0, "acc1")


def test_zero_loops_plans_one_round():
    config = KeepOnlineConfig(interval=60, loops=0)
    assert len(beats(config, ["acc1"])) == 1


def test_dry_run_prints_plan(ctx, capsys):
    config = KeepOnlineConfig(account_labels=[], interval=300, loops=2)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert output.count("heartbeat account=") == 4
    plan = [line for line in output.splitlines() if line.startswith("PLAN: ")]
    assert plan and plan[0].startswith("PLAN: 4 heartbeats, 2 accounts")
    assert "dry-run: no session was opened" in output


def test_empty_pool_fails_cleanly(ctx, capsys, store):
    ctx.store._conn.execute("DELETE FROM accounts")
    ctx.store._conn.commit()
    assert run(KeepOnlineConfig(), ctx) == 2
    assert "SESSION_NOT_FOUND" in capsys.readouterr().out
