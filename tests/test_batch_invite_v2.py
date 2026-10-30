"""#17 batch invite v2: caps, the PEER_FLOOD/TIMEOUT export and success cap.

v1.3.9 adds the reference client's own sentences for this feature: the plan
lines (``Account #%1: %2 targets, %3 contacts``), the per-group cap notices
(``Target %1 reached per-group success limit (%2/%3).``), the report trio
(``success_invites_*.txt`` / ``failed_invites_*.txt`` / ``invite_report_*.txt``)
and the three completion verdicts (inviters > limit > targets).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ogtool.config import ConfigError
from ogtool.features.batch_invite_v2 import InviteV2Config, build_jobs, run


def test_round_robin_handlers_over_contacts():
    jobs = build_jobs(["g1"], ["m1", "m2", "m3"], ["h1", "h2"])
    assert [handler for handler, _, _ in jobs] == ["h1", "h2", "h1"]


def test_per_target_cap_trims():
    jobs = build_jobs(["g1", "g2"], ["m1", "m2", "m3"], ["h"], target_success_cap=1)
    assert [(group, member) for _, group, member in jobs] == [
        ("g1", "m1"),
        ("g2", "m1"),
    ]


def test_exec_cap_trims():
    jobs = build_jobs(["g1"], ["m1", "m2", "m3"], ["h"], exec_cap=2)
    assert len(jobs) == 2


def test_flood_and_timeout_are_exported(ctx, scripted, tmp_path: Path, capsys):
    # Two flood retries are spent on @c1 before the cap gives up, then
    # @c2 answers a timeout.
    scripted(["PEER_FLOOD", "PEER_FLOOD", "PEER_FLOOD", "TIMEOUT"])
    path = tmp_path / "flood_timeout.txt"
    config = InviteV2Config(
        targets=["@g1"], contacts=["@c1", "@c2"], export_flood_timeout=str(path)
    )
    run(config, ctx)
    output = capsys.readouterr().out
    assert "PEER_FLOOD: 1" in output
    assert "TIMEOUT: 1" in output
    body = path.read_text()
    assert "PEER_FLOOD\t@c1" in body
    assert "TIMEOUT\t@c2" in body


def test_success_cap_stops_the_queue(ctx, capsys):
    config = InviteV2Config(
        targets=["@g1"], contacts=["@c1", "@c2", "@c3"], success_cap=1
    )
    run(config, ctx)
    output = capsys.readouterr().out
    assert "stopped: success_cap (1)" in output


def test_missing_target_is_a_config_error():
    with pytest.raises(ConfigError):
        InviteV2Config.from_dict({"batch_invite_v2": {"contacts": ["@c1"]}}, None)  # type: ignore[arg-type]


def test_a_live_run_waits_the_invite_interval(ctx, live, ok_sender, slept):
    live(ok_sender)
    config = InviteV2Config(
        targets=["@g1"],
        contacts=["@c1", "@c2", "@c3"],
        request_interval=4.0,
        invite_interval_min=6.0,
        invite_interval_max=6.0,
    )
    assert run(config, ctx) == 0
    assert slept == [6.0, 6.0]


def test_a_dry_run_invites_without_waiting(ctx, slept):
    config = InviteV2Config(
        targets=["@g1"],
        contacts=["@c1", "@c2"],
        request_interval=4.0,
        invite_interval_min=6.0,
        invite_interval_max=9.0,
    )
    assert run(config, ctx) == 0
    assert slept == []


def test_a_target_is_rested_after_its_own_flood_ceiling(ctx, scripted, capsys):
    """``peerFloodTargetMaxRetries`` is a per-TARGET ceiling (dump 1744470).

    The per-invitee cap already existed; this one is about the group: when a
    target keeps answering PEER_FLOOD, the rest of its contacts are skipped
    instead of being spent on a target that is plainly closed for now.
    """
    scripted(["PEER_FLOOD", "PEER_FLOOD", "PEER_FLOOD"])
    config = InviteV2Config(
        targets=["@g1"],
        contacts=["@c1", "@c2", "@c3"],
        peer_flood_target_max_retries=1,
        invitee_flood_cap=0,
    )
    run(config, ctx)
    output = capsys.readouterr().out
    assert "target rested: @g1" in output
    assert "skipped @c2 in @g1" in output
    assert "skipped @c3 in @g1" in output


def test_a_zero_target_ceiling_never_rests_a_target(ctx, scripted, capsys):
    scripted(["PEER_FLOOD", "ok"])
    config = InviteV2Config(
        targets=["@g1"],
        contacts=["@c1", "@c2"],
        peer_flood_target_max_retries=0,
        invitee_flood_cap=0,
    )
    run(config, ctx)
    output = capsys.readouterr().out
    assert "target rested" not in output
    assert "-> ok" in output


def test_a_handler_is_rested_after_its_own_flood_ceiling(ctx, scripted, capsys):
    """``floodCap`` is the handler's patience, not the invitee's retry count.

    ``inviteeFloodCap`` says how many times one contact may be retried;
    ``floodCap`` says after how many PEER_FLOOD answers the handler itself stops
    for the rest of the run (dump: separate rows on the page).
    """
    scripted(["PEER_FLOOD", "PEER_FLOOD", "PEER_FLOOD"])
    config = InviteV2Config(
        targets=["@g1"],
        contacts=["@c1", "@c2", "@c3"],
        flood_cap=1,
        invitee_flood_cap=0,
        peer_flood_target_max_retries=0,
    )
    run(config, ctx)
    output = capsys.readouterr().out
    # Round robin puts @c1 and @c3 on acc1, @c2 on acc2: the rested handler is
    # the one whose remaining contact gets skipped.
    assert "handler rested: acc1" in output
    assert "skipped @c3 in @g1 (handler rested" in output


def test_a_zero_handler_ceiling_never_rests_it(ctx, scripted, capsys):
    scripted(["PEER_FLOOD", "ok"])
    config = InviteV2Config(
        targets=["@g1"],
        contacts=["@c1", "@c2"],
        flood_cap=0,
        invitee_flood_cap=0,
        peer_flood_target_max_retries=0,
    )
    run(config, ctx)
    assert "handler rested" not in capsys.readouterr().out


def test_an_empty_plan_is_an_answer_not_a_crash(ctx, capsys):
    """No lanes to build used to be IndexError: lanes[0] on an empty list."""
    from ogtool.features.batch_invite_v2 import run

    config = InviteV2Config(targets=["@g1"], contacts=[])
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "no jobs: nothing to invite" in output
    assert "PLAN: 0 invites" in output


def test_monitor_mode_invites_new_joiners_only(ctx, capsys, monkeypatch):
    """Round 1 builds the baseline; joiners after that get invited."""
    from types import SimpleNamespace

    from ogtool.features.batch_invite_v2 import InviteV2Config, _monitor

    monkeypatch.setattr("ogtool.features.batch_invite_v2.pause", lambda *a, **k: None)
    monkeypatch.setattr("ogtool.features.batch_invite_v2.time.monotonic", lambda: 0.0)

    class Members:
        def get_participants(self, account, target, limit=0):
            return SimpleNamespace(ok=True, code="", detail="", items=["@old1"])

        def close(self):
            return None

        def invite(self, handler, target, member):
            _monitor.CALLS.append((handler, target.ref, member.ref))
            return SimpleNamespace(
                ok=True, code="ok", detail="", items=[], message_id=0
            )

    _monitor.CALLS = []
    ctx._sender = Members()
    ctx.live = True
    # monitor_seconds=0 polls until interrupted; one poll then Ctrl+C
    sides = iter([KeyboardInterrupt()])

    def fake_pause(*a, **k):
        raise next(sides)

    monkeypatch.setattr("ogtool.features.batch_invite_v2.pause", fake_pause)
    config = InviteV2Config(
        account_labels=["acc1"],
        monitor_group="https://t.me/c/3965371671",
        targets=["https://t.me/c/3965371671"],
        monitor_skip_existing=True,
    )
    code = _monitor(config, ctx)
    out = capsys.readouterr().out
    assert code == 130
    assert "baseline" in out
    assert _monitor.CALLS == []  # the old member was NOT invited


def test_monitor_invites_who_joins_after_the_baseline(ctx, capsys, monkeypatch):
    from types import SimpleNamespace

    from ogtool.features.batch_invite_v2 import InviteV2Config, _monitor

    roster = [
        [SimpleNamespace(ref="@old1")],
        [SimpleNamespace(ref="@old1"), SimpleNamespace(ref="@new1")],
        [SimpleNamespace(ref="@old1"), SimpleNamespace(ref="@new1")],
    ]
    rounds = iter(roster)

    class Watcher:
        def get_participants(self, account, target, limit=0):
            return SimpleNamespace(
                ok=True, code="", detail="", items=[u.ref for u in next(rounds)]
            )

        def close(self):
            return None

        def invite(self, handler, target, member):
            _monitor.CALLS.append((handler, target.ref, member.ref))
            return SimpleNamespace(
                ok=True, code="ok", detail="", items=[], message_id=0
            )

    _monitor.CALLS = []
    ctx._sender = Watcher()
    ctx.live = True
    stops = iter([False, False, False, KeyboardInterrupt()])

    def fake_pause(*a, **k):
        if next(stops):
            raise KeyboardInterrupt()

    monkeypatch.setattr("ogtool.features.batch_invite_v2.pause", fake_pause)
    monkeypatch.setattr("ogtool.features.batch_invite_v2.time.monotonic", lambda: 0.0)
    config = InviteV2Config(
        account_labels=["acc1"],
        monitor_group="https://t.me/c/3965371671",
        targets=["https://t.me/c/3965371671"],
        monitor_skip_existing=True,
    )
    # interrupted with invites already landed: a clean exit, not a failure
    assert _monitor(config, ctx) == 0
    out = capsys.readouterr().out
    assert "1 new member(s)" in out
    assert _monitor.CALLS and _monitor.CALLS[0][2] == "@new1"


def test_resume_skips_already_invited_triples(ctx, capsys, store):
    """--resume drops (handler, group<-contact) pairs the receipts carry."""
    from ogtool.features._common import open_task
    from ogtool.features.batch_invite_v2 import FEATURE, InviteV2Config, build_jobs, run

    config = InviteV2Config(
        account_labels=["acc1"],
        targets=["https://t.me/c/3965371671"],
        contacts=["@new1", "@new2"],
        resume=True,
    )
    task = open_task(ctx, "prior-invite", FEATURE, {}, 1)
    store.add_result(task.id, "acc1", "https://t.me/c/3965371671<-@new1", "sent")

    jobs = build_jobs(config.targets, config.contacts, ["acc1"])
    assert len(jobs) == 2
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "RESUME: skipping 1 already-invited contact(s); 1 left" in out


# --- v1.3.9: the reference client's own sentences ------------------------


def test_the_plan_names_every_account_and_its_counts(ctx, capsys):
    """``Account #%1: %2 targets, %3 contacts`` and its skipped twin."""
    config = InviteV2Config(targets=["@g1"], contacts=["@c1"])
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "Account #acc1: 1 targets, 1 contacts" in out
    assert "Account #acc2 skipped: no contacts" in out


def test_a_capped_group_is_announced_once_and_the_rest_is_skipped(ctx, capsys):
    """``Target %1 reached per-group success limit (%2/%3).``"""
    config = InviteV2Config(
        targets=["@g1"],
        contacts=["@c1", "@c2", "@c3"],
        target_success_cap=1,
        exec_cap=3,
        limit_count_type="exec",
    )
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert out.count("Target @g1 reached per-group success limit (1/1).") == 1
    assert (
        out.count(
            "All available worker accounts reached their limit for @g1, "
            "skipping remaining members."
        )
        == 1
    )
    assert "Processed: 1/3" in out
    assert "#acc1 Batch invite 1 success, 0 failed -> 1 targets" in out
    assert "#acc2 Batch invite 0 success, 0 failed -> 0 targets" in out


def test_the_report_trio_is_written_into_the_output_dir(ctx, tmp_path, capsys):
    """``%1/success_invites_%2_task_%3.txt`` and its two siblings."""
    base = tmp_path / "out"
    config = InviteV2Config(
        account_labels=["acc1"],
        targets=["@g1"],
        contacts=["@c1", "@c2"],
        output_dir=str(base),
    )
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "Saved 2 successful invites to" in out
    assert "Saved 0 failed invites to" in out
    assert "Generated report: " in out

    success = list(base.glob("success_invites_*.txt"))
    failed = list(base.glob("failed_invites_*.txt"))
    report = list(base.glob("invite_report_*.txt"))
    assert len(success) == 1
    assert len(failed) == 1
    assert len(report) == 1

    body = success[0].read_text(encoding="utf-8")
    assert "#acc1 @c1 -> @g1 (success)" in body
    assert "#acc1 @c2 -> @g1 (success)" in body

    text = report[0].read_text(encoding="utf-8")
    assert "=== Batch Invite Report ===" in text
    assert "Task ID: " in text
    assert "Target Groups: 1" in text
    assert "Members to Invite: 2" in text
    assert "Total Invitations: 2" in text
    assert "Processed: 2" in text
    assert "Successful: 2" in text
    assert "Failed: 0" in text
    assert "Worker Accounts: acc1" in text
    assert "Interval: 0 millsseconds" in text
    assert "Request Interval: 8000 milliseconds" in text
    assert "Invite Interval Range: 8000-15000" in text


def test_a_failed_invite_keeps_its_code_in_the_export(ctx, scripted, tmp_path, capsys):
    """``#%1 %2 -> %3 (failed: %4)``"""
    scripted(["USER_NOT_FOUND"])
    base = tmp_path / "out"
    config = InviteV2Config(
        account_labels=["acc1"],
        targets=["@g1"],
        contacts=["@c1"],
        output_dir=str(base),
    )
    assert run(config, ctx) == 1
    out = capsys.readouterr().out
    assert "#acc1 @c1 -> @g1 (failed: USER_NOT_FOUND)" in out
    body = list(base.glob("failed_invites_*.txt"))[0].read_text(encoding="utf-8")
    assert "#acc1 @c1 -> @g1 (failed: USER_NOT_FOUND)" in body


def test_resume_names_the_targets_the_receipts_already_cover(ctx, store, capsys):
    """``Skip processed target: %1 -> %2 (result: %3)``"""
    from ogtool.features._common import open_task
    from ogtool.features.batch_invite_v2 import FEATURE

    config = InviteV2Config(
        account_labels=["acc1"],
        targets=["https://t.me/c/3965371671"],
        contacts=["@new1", "@new2"],
        resume=True,
    )
    task = open_task(ctx, "prior-invite", FEATURE, {}, 2)
    store.add_result(task.id, "acc1", "https://t.me/c/3965371671<-@new1", "sent")
    store.add_result(task.id, "acc1", "https://t.me/c/3965371671<-@new2", "sent")

    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "RESUME: skipping 2 already-invited contact(s); 0 left" in out
    assert (
        "Skip processed target: acc1 -> https://t.me/c/3965371671 (result: sent)" in out
    )
    assert "All targets already processed (result: sent)" in out


def test_the_verdict_is_targets_when_nothing_capped(
    ctx, live, ok_sender, slept, capsys
):
    """``Completed task %1: All targets processed, ...``"""
    live(ok_sender)
    config = InviteV2Config(targets=["@g1"], contacts=["@c1"])
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert re.search(
        r"Completed task \d+: All targets processed, "
        r"processed 1, success 1, failed 0",
        out,
    )
    assert re.search(r"Batch Invite Task \d+ Completed\.", out)


def test_the_verdict_is_limit_when_a_cap_stops_the_queue(
    ctx, live, ok_sender, slept, capsys
):
    """``Completed task %1: All accounts reached limit, ...``"""
    live(ok_sender)
    config = InviteV2Config(targets=["@g1"], contacts=["@c1", "@c2"], success_cap=1)
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "stopped: success_cap (1)" in out
    assert re.search(
        r"Completed task \d+: All accounts reached limit, "
        r"processed 1, success 1, failed 0",
        out,
    )


class FloodTransport:
    """Every invite answers PEER_FLOOD, so every handler rests."""

    live = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    def invite(self, account, group, member):
        from ogtool.senders import SendResult

        self.calls.append(account)
        return SendResult(
            ok=False, code="PEER_FLOOD", detail="peer flood", retryable=True
        )

    def close(self) -> None:
        return None


def test_the_verdict_is_inviters_when_every_handler_rests(ctx, live, slept, capsys):
    """``Completed task %1: All inviter accounts exhausted, ...``"""
    live(FloodTransport())
    config = InviteV2Config(
        targets=["@g1"],
        contacts=["@c1", "@c2"],
        flood_cap=1,
        invitee_flood_cap=0,
        peer_flood_target_max_retries=0,
    )
    assert run(config, ctx) == 1
    out = capsys.readouterr().out
    assert re.search(
        r"Completed task \d+: All inviter accounts exhausted, "
        r"processed 2, success 0, failed 2",
        out,
    )


def test_an_empty_member_list_says_empty_members(ctx, capsys):
    """``Empty members`` plus the no-executable-account fallback."""
    config = InviteV2Config(targets=["@g1"], contacts=[])
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "Empty members" in out
    assert "No executable accounts found (no owned targets or no contacts)" in out
    assert "PLAN: 0 invites, failed 0" in out


def test_nothing_configured_says_so(ctx, capsys):
    """``No members or targets configured``"""
    config = InviteV2Config(targets=[], contacts=[])
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "No members or targets configured" in out
    assert "no jobs: nothing to invite" in out
    assert "PLAN: 0 invites, failed 0" in out


def test_the_reference_skip_key_turns_on_resume():
    """``skipProcessedTargets`` is the reference spelling of ``resume``."""
    raw = {
        "batch_invite_v2": {
            "targets": ["g1"],
            "contacts": ["@c1"],
            "skipProcessedTargets": True,
        }
    }
    config = InviteV2Config.from_dict(raw, None)  # type: ignore[arg-type]
    assert config.resume is True


def test_resume_stays_off_without_the_key():
    raw = {"batch_invite_v2": {"targets": ["g1"], "contacts": ["@c1"]}}
    config = InviteV2Config.from_dict(raw, None)  # type: ignore[arg-type]
    assert config.resume is False


def test_the_lane_stops_on_its_own_exec_cap(ctx, capsys):
    """``stopped: exec_cap (N)`` is the lane's own guard.

    ``build_jobs`` already trims the queue down to the cap, so this branch is
    only reachable when the lane is called directly - which is exactly what a
    unit test does.
    """
    from ogtool.features._common import open_task
    from ogtool.features.batch_invite_v2 import FEATURE, _invite_lane
    from ogtool.limiter import PeerFloodBackoff

    config = InviteV2Config(targets=["@g1"], contacts=["@c1", "@c2"], exec_cap=1)
    task = open_task(ctx, "lane-guard", FEATURE, {}, 2)
    lane = [("acc1", "@g1", "@c1"), ("acc1", "@g1", "@c2")]
    result = _invite_lane(
        ctx.sender(), ctx, task, lane, len(lane), config, PeerFloodBackoff(), []
    )
    out = capsys.readouterr().out
    assert result.ok == 1
    assert result.stop_reason == "limit"
    assert "stopped: exec_cap (1)" in out
