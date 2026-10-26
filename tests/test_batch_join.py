"""#8 batch join: assignment, caps, retry and failure classification."""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features.batch_join import FLAGS, JoinConfig, assign, run
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


def test_leave_interval_paces_leaves_not_joins():
    """``leaveInterval`` is the leave page's own pace."""
    from ogtool.features.batch_join import gap
    from ogtool.runner import Job

    config = JoinConfig(request_interval=2.0, leave_interval=30.0)
    job = Job(account="a", subject="@g1")
    assert gap(config, job, None, leaving=False) == 2.0
    assert gap(config, job, None, leaving=True) == 30.0


def test_the_account_interval_still_wins_when_it_is_longer():
    from ogtool.features.batch_join import gap
    from ogtool.runner import Job

    config = JoinConfig(account_interval=60.0, leave_interval=30.0)
    first = Job(account="a", subject="@g1")
    same = Job(account="a", subject="@g2")
    assert gap(config, same, first, leaving=True) == 60.0


def test_max_join_count_stops_the_assignment():
    """``maxJoinCount`` is the page's ceiling on how many chats one run joins."""
    from ogtool.features.batch_join import assign

    targets = parse_many(["@g1", "@g2", "@g3", "@g4"], "group")
    pairs = assign(["a", "b"], targets, 0, 0, max_join_count=3)
    assert len(pairs) == 3
    assert [target.ref for _, target in pairs] == ["@g1", "@g2", "@g3"]


def test_zero_max_join_count_means_no_ceiling():
    from ogtool.features.batch_join import assign

    targets = parse_many(["@g1", "@g2", "@g3"], "group")
    assert len(assign(["a"], targets, 0, 0, 0)) == 3


def test_max_join_count_never_overrides_the_per_account_cap():
    from ogtool.features.batch_join import assign

    targets = parse_many(["@g1", "@g2", "@g3"], "group")
    pairs = assign(["a", "b"], targets, 1, 0, max_join_count=3)
    assert len(pairs) == 2


def test_each_account_joins_all_groups_is_a_cross_product():
    """``joinMode``: the page's two modes are rotation or cross product."""
    from ogtool.features.batch_join import every_account

    targets = parse_many(["@g1", "@g2"], "group")
    pairs = every_account(["a", "b"], targets, 0, 0, 0)
    assert [(account, target.ref) for account, target in pairs] == [
        ("a", "@g1"), ("a", "@g2"), ("b", "@g1"), ("b", "@g2")]


def test_all_groups_honours_the_per_account_cap_and_exec_cap():
    from ogtool.features.batch_join import every_account

    targets = parse_many(["@g1", "@g2", "@g3"], "group")
    assert len(every_account(["a", "b"], targets, 1, 0, 0)) == 2
    assert len(every_account(["a", "b"], targets, 0, 3, 0)) == 3
    assert len(every_account(["a", "b"], targets, 0, 0, 1)) == 1


def test_join_mode_all_prints_the_cross_product(ctx, capsys):
    from ogtool.features.batch_join import JoinConfig, run

    config = JoinConfig(targets=parse_many(["@g1", "@g2"], "group"),
                        join_mode="all", request_interval=0,
                        account_interval=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "join mode: each account joins all groups (4 join(s))" in output


def test_the_per_account_interval_is_a_range():
    """``perAccountJoinIntervalMin/Max``: a draw, not a fixed number."""
    from ogtool.features.batch_join import gap
    from ogtool.runner import Job

    config = JoinConfig(request_interval=0, account_interval=10,
                        per_account_interval_min=30, per_account_interval_max=60)
    first = Job(account="a", subject="@g1")
    same = Job(account="a", subject="@g2")
    values = {gap(config, same, first) for _ in range(20)}
    assert min(values) >= 30
    assert max(values) <= 60
    assert len(values) > 1


def test_a_per_account_range_below_one_second_is_a_config_error():
    import pytest

    from ogtool.config import ConfigError
    from ogtool.features.batch_join import JoinConfig

    class Settings:
        pass

    with pytest.raises(ConfigError):
        JoinConfig.from_dict({"batch_join": {
            "targets": ["@g1"], "per_account_join_interval_min_ms": 500,
        }}, Settings())


class CountingSender(JoinSender):
    """A join sender whose accounts already sit in a known number of chats."""

    def __init__(self, counts: dict[str, int]) -> None:
        super().__init__([])
        self.counts = counts
        self.joins: list[tuple[str, str]] = []

    def chat_count(self, account: str):
        from ogtool.senders import SendResult

        result = SendResult(ok=True)
        result.count = self.counts.get(account, 0)
        return result

    def join(self, account: str, target: object):
        from ogtool.senders import SendResult

        self.joins.append((account, target.ref))
        return SendResult(ok=True)


def test_a_full_account_takes_no_more_chats(ctx, live, capsys):
    """``maxJoinedChatsPerAccount`` asks the account how full it already is."""
    sender = CountingSender({"acc1": 500, "acc2": 10})
    live(sender)
    config = JoinConfig(targets=parse_many(["@g1", "@g2"], "group"),
                        max_joined_chats=400, request_interval=0,
                        account_interval=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "account full: acc1 already in 500 chat(s)" in output
    assert all(account != "acc1" for account, _ in sender.joins)
    rows = ctx.store.results_for(ctx.task_id)
    assert any(row["error_code"] == "ACCOUNT_FULL" for row in rows)


def test_the_cap_is_not_asked_offline(ctx, capsys):
    """A dry run has no dialogs to count, so it must not invent a number."""
    config = JoinConfig(targets=parse_many(["@g1"], "group"),
                        max_joined_chats=1, request_interval=0,
                        account_interval=0)
    assert run(config, ctx) == 0
    assert "account full" not in capsys.readouterr().out


def _settings():
    return type("S", (), {"runtime": type("R", (), {
        "clamp_threads": staticmethod(lambda value: value)})()})()


def test_leave_mode_is_gone_and_request_timeout_says_what_it_does():
    """leaveMode never did anything: leaving is chosen by operation_type and
    the sender picks the request per chat kind. The CLI flag is gone. The
    request timeout keeps its validation - the plan shows it, and no Telethon
    entry point accepts a per-request timeout to wire it deeper."""
    assert all(flag.dest != "leave_mode" for flag in FLAGS)
    timeout = [flag for flag in FLAGS if flag.dest == "request_timeout"][0]
    assert "no per-request timeout" in timeout.help
    with pytest.raises(ConfigError):
        JoinConfig.from_dict({"batch_join": {"request_timeout": 0.5}},
                             _settings())
