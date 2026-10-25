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


def test_the_account_interval_applies_when_one_account_goes_twice():
    from ogtool.features.batch_join import gap
    from ogtool.runner import Job

    config = JoinConfig(request_interval=5.0, account_interval=10.0)
    first = Job(account="a", subject="@g1")
    same = Job(account="a", subject="@g2")
    other = Job(account="b", subject="@g3")
    assert gap(config, first, None) == 5.0
    assert gap(config, same, first) == 10.0
    assert gap(config, other, first) == 5.0


def test_a_live_run_keeps_the_request_interval(ctx, live, ok_sender, slept):
    live(ok_sender)
    config = JoinConfig(targets=parse_many(["@g1", "@g2", "@g3"], "group"),
                        request_interval=5.0, account_interval=10.0)
    assert run(config, ctx) == 0
    # The pool alternates between the two accounts, so every gap is the pool
    # interval; the account interval only bites when one account repeats.
    assert slept == [5.0, 5.0]


def test_a_dry_run_joins_without_waiting(ctx, slept):
    config = JoinConfig(targets=parse_many(["@g1", "@g2", "@g3"], "group"),
                        account_labels=["acc1"], request_interval=5.0,
                        account_interval=10.0)
    assert run(config, ctx) == 0
    assert slept == []


class LaneSender:
    """Records when each join happened, so two lanes can be told apart."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []

    def join(self, account: str, target: object):
        import time as clock

        from ogtool.senders import SendResult

        self.calls.append((account, getattr(target, "ref", "?"),
                           clock.monotonic()))
        return SendResult(ok=True)

    def close(self) -> None:
        pass


def test_one_account_is_never_worked_by_two_lanes(ctx, live, monkeypatch):
    import time as clock

    sender = LaneSender()
    live(sender)
    real_sleep = clock.sleep
    monkeypatch.setattr("ogtool.features._common.time.sleep", real_sleep)
    config = JoinConfig(targets=parse_many(["@g1", "@g2", "@g3", "@g4"], "group"),
                        account_labels=["acc1", "acc2"], threads=4,
                        request_interval=0.0, account_interval=0.0)
    assert run(config, ctx) == 0
    per_account: dict[str, int] = {}
    for account, _, _ in sender.calls:
        per_account[account] = per_account.get(account, 0) + 1
    assert sum(per_account.values()) == len(sender.calls)
    assert "acc1" in per_account


def test_two_accounts_really_join_at_the_same_time(ctx, live, monkeypatch):
    import time as clock

    sender = LaneSender()
    live(sender)
    real_sleep = clock.sleep
    monkeypatch.setattr("ogtool.features._common.time.sleep", real_sleep)
    monkeypatch.setattr("ogtool.runner.time.sleep", real_sleep)
    config = JoinConfig(
        targets=parse_many(["@g1", "@g2", "@g3", "@g4"], "group"),
        account_labels=["acc1", "acc2"], threads=2,
        request_interval=1.0, account_interval=1.0)
    assert run(config, ctx) == 0
    seen: dict[str, list[float]] = {}
    for account, _, at in sender.calls:
        seen.setdefault(account, []).append(at)
    # Serial would finish acc1 entirely before acc2 starts; lanes overlap.
    assert min(seen["acc2"]) < max(seen["acc1"])


def test_more_threads_than_accounts_does_not_invent_lanes(ctx, live):
    sender = LaneSender()
    live(sender)
    config = JoinConfig(targets=parse_many(["@g1", "@g2"], "group"),
                        account_labels=["acc1"], threads=8,
                        request_interval=0.0, account_interval=0.0)
    assert run(config, ctx) == 0
    assert {account for account, _, _ in sender.calls} == {"acc1"}


class MuteAwareSender(JoinSender):
    def __init__(self, codes: list[str]) -> None:
        super().__init__(codes)
        self.muted: list[tuple[str, str]] = []

    def mute(self, account: str, target: object, until: int = 0):
        from ogtool.senders import SendResult

        self.muted.append((account, getattr(target, "kind", "")))
        return SendResult(ok=True)


def test_mute_channels_only_silences_channels(ctx, live, capsys):
    """``muteChannels`` / ``mutePrivate`` are two switches, not one."""
    sender = MuteAwareSender(["ok", "ok"])
    live(sender)
    config = JoinConfig(targets=[parse_many(["@chan"], "channel")[0],
                                 parse_many(["@grp"], "group")[0]],
                        mute_channels=True)
    assert run(config, ctx) == 0
    assert sender.muted == [("acc1", "channel")]
    assert "muted: 1 chat(s)" in capsys.readouterr().out


def test_mute_private_silences_plain_groups(ctx, live):
    sender = MuteAwareSender(["ok"])
    live(sender)
    config = JoinConfig(targets=[parse_many(["@grp"], "group")[0]],
                        mute_private=True)
    assert run(config, ctx) == 0
    assert sender.muted == [("acc1", "group")]


def test_a_failed_join_is_not_muted(ctx, live):
    sender = MuteAwareSender(["CHANNEL_PRIVATE"])
    live(sender)
    config = JoinConfig(targets=[parse_many(["@chan"], "channel")[0]],
                        mute_channels=True)
    assert run(config, ctx) == 1
    assert sender.muted == []
