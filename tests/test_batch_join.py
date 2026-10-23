"""#8 batch join: assignment, caps, retry and failure classification."""

from __future__ import annotations

from ogtool.features.batch_join import JoinConfig, assign, run
from ogtool.targets import parse_many


def test_assign_round_robins_accounts():
    groups = parse_many(["@g1", "@g2", "@g3", "@g4"], "group")
    pairs = assign(["a", "b"], groups, 0, 0)
    assert [account for account, _ in pairs] == ["a", "b", "a", "b"]
    assert [target.ref for _, target in pairs] == ["@g1", "@g2", "@g3", "@g4"]


def test_per_account_cap_trims_the_queue():
    groups = parse_many([f"@g{i}" for i in range(6)], "group")
    pairs = assign(["a", "b"], groups, per_account_cap=2, exec_cap=0)
    assert len(pairs) == 4
    assert [account for account, _ in pairs].count("a") == 2
    assert [account for account, _ in pairs].count("b") == 2


def test_exec_cap_trims_too():
    groups = parse_many([f"@g{i}" for i in range(6)], "group")
    assert len(assign(["a"], groups, 0, exec_cap=3)) == 3


def test_no_targets_is_a_usage_error(ctx, capsys):
    assert run(JoinConfig(), ctx) == 2
    assert "no targets" in capsys.readouterr().out


def test_peer_flood_is_retried_then_succeeds(ctx, scripted, capsys):
    scripted(["PEER_FLOOD", "ok"])
    config = JoinConfig(targets=parse_many(["@g1"], "group"),
                        flood_cap=2)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "PEER_FLOOD backoff" in output
    assert "ok: 1" in output
    assert "failed: 0" in output


def test_terminal_answer_is_counted_as_failure(ctx, scripted, capsys):
    scripted(["CHANNEL_PRIVATE"])
    config = JoinConfig(targets=parse_many(["@g1"], "group"))
    assert run(config, ctx) == 1
    output = capsys.readouterr().out
    assert "CHANNEL_PRIVATE" in output
    assert "failed: 1" in output


def test_duplicate_targets_are_collapsed():
    groups = parse_many(["@g1", "@g1", "https://t.me/g1"], "group")
    assert len(groups) == 1


class JoinSender:
    """Feeds the loop a scripted answer per join, as the live sender would."""

    def __init__(self, codes: list[str]) -> None:
        self.codes = list(codes)
        self.calls = 0

    def join(self, account: str, target: object):
        from ogtool.senders import result_for

        code = self.codes[self.calls] if self.calls < len(self.codes) else "ok"
        self.calls += 1
        return result_for(code)

    def close(self) -> None:
        pass


def test_a_live_run_records_the_real_outcome_of_each_join(ctx, live, capsys):
    live(JoinSender(["ok", "CHANNEL_PRIVATE"]))
    config = JoinConfig(targets=parse_many(["@g1", "@g2"], "group"))
    assert run(config, ctx) == 1
    rows = ctx.store.results_for(ctx.task_id)
    assert [row["status"] for row in rows] == ["sent", "failed"]
