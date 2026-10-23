"""The shared batch loop: caps, retries and the flood policy."""

from __future__ import annotations

from ogtool.progress import Stats
from ogtool.runner import Job, execute
from ogtool.senders import SendResult
from ogtool.simulate import PEER_FLOOD, Script


def _ctx_script(ctx, codes):
    ctx.script = Script(codes)
    ctx.close()
    ctx._sender = None
    return ctx


def test_every_job_succeeds_by_default(ctx, capsys):
    ctx = _ctx_script(ctx, [])
    jobs = [Job("a", "s1"), Job("a", "s2")]
    outcome = execute(jobs, ctx, lambda job: ctx.sender().join(
        job.account, _target(job.subject)), total=2)
    assert outcome.stats.ok == 2
    assert outcome.stats.processed == 2
    assert "processed=2/2" in outcome.stats.summary()


def test_exec_cap_counts_attempts(ctx):
    ctx = _ctx_script(ctx, [])
    jobs = [Job("a", f"s{i}") for i in range(5)]
    outcome = execute(jobs, ctx, lambda job: SendResult(ok=True), exec_cap=2)
    assert outcome.stats.processed == 2
    assert outcome.stopped == "exec_cap"


def test_success_cap_stops_early(ctx):
    ctx = _ctx_script(ctx, [])
    jobs = [Job("a", f"s{i}") for i in range(5)]
    outcome = execute(jobs, ctx, lambda job: SendResult(ok=True), success_cap=3)
    assert outcome.stats.ok == 3
    assert outcome.stopped == "success_cap"


def test_peer_flood_is_retried_up_to_the_cap(ctx, capsys):
    ctx = _ctx_script(ctx, [PEER_FLOOD, PEER_FLOOD, PEER_FLOOD])
    outcome = execute([Job("a", "s1")], ctx,
                      lambda job: ctx.sender().join(job.account,
                                                    _target(job.subject)),
                      flood_cap=2)
    assert outcome.stats.failed == 1
    assert outcome.flood == ["s1"]
    assert outcome.waited > 0
    assert "PEER_FLOOD backoff" in capsys.readouterr().out


def test_retryable_code_is_retried_once(ctx, capsys):
    ctx = _ctx_script(ctx, ["TIMEOUT", "ok"])
    outcome = execute([Job("a", "s1")], ctx,
                      lambda job: ctx.sender().join(job.account,
                                                    _target(job.subject)),
                      retry_cap=1)
    assert outcome.stats.ok == 1
    assert outcome.timeouts == []
    assert "retry TIMEOUT" in capsys.readouterr().out


def test_terminal_code_is_not_retried(ctx):
    ctx = _ctx_script(ctx, [])
    calls = {"n": 0}

    def once(job: Job) -> SendResult:
        calls["n"] += 1
        return SendResult(ok=False, code="USER_ID_INVALID", retryable=False)

    outcome = execute([Job("a", "s1")], ctx, once, retry_cap=3)
    assert calls["n"] == 1
    assert outcome.missing == ["s1"]


def test_stats_keep_a_per_code_tally():
    stats = Stats(total=3)
    stats.record(False, "PEER_FLOOD")
    stats.record(False, "PEER_FLOOD")
    stats.record(True)
    assert stats.codes == {"PEER_FLOOD": 2}
    assert stats.ok == 1 and stats.failed == 2


def _target(ref: str):
    from ogtool.targets import parse_many

    return parse_many([ref], "group")[0]
