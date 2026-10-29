"""The checkme post-join verification protocol, client side.

The reference's own pieces, each pinned to a dump line: the endpoint
(:1739935), the answer's decision key and the six decisions
(:1739958-1739980), the give-up lines (:1740027-1740029), the submit /
status / finish log lines (:1740039-1740047), and the four configuration
keys (:1743074-1743077). The service is never contacted by a test: the
``_post`` seam is patched, which is also why the state machine is
testable without a socket.
"""

from __future__ import annotations

import json

from ogtool.config import CheckmeConfig
from ogtool.features import checkme
from ogtool.features.checkme import (
    CheckmeExecutor,
    CheckmeOutcome,
    TelegramExecutor,
    verify_join,
)


class Recorder(CheckmeExecutor):
    """An executor that records the actions it is asked to perform."""

    def __init__(self, fail_on: str = "") -> None:
        self.seen: list[dict] = []
        self.fail_on = fail_on

    def perform(self, action: dict) -> tuple[bool, str]:
        self.seen.append(action)
        kind = str(action.get("action", ""))
        if kind == self.fail_on:
            return False, f"{kind} failed on purpose"
        return True, f"{kind} done"


def _config(**overrides) -> CheckmeConfig:
    base = {
        "base_url": "http://127.0.0.1:9",
        "enabled": True,
        "max_round": 5,
        "delay_sec": 0.0,
    }
    base.update(overrides)
    return CheckmeConfig(**base)


def _answers(*bodies: str):
    """A ``_post`` stub answering the given bodies in order."""
    calls: list[dict] = []

    def fake_post(url: str, payload: dict, timeout: float) -> tuple[int, str]:
        calls.append(payload)
        index = min(len(calls) - 1, len(bodies) - 1)
        return 200, bodies[index]

    return fake_post, calls


def _answer(**fields) -> str:
    return json.dumps(fields)


def _run(config, ctx, executor=None, monkeypatch=None, post=None):
    if post is not None and monkeypatch is not None:
        monkeypatch.setattr(checkme, "_post", post)
    return verify_join(config, ctx, "acc1", 0, "@g1", 42, executor=executor)


def test_stop_on_round_one_verifies(ctx, monkeypatch):
    post, calls = _answers(_answer(checkme="STOP", reason="clean"))
    monkeypatch.setattr(checkme, "_post", post)
    outcome = _run(_config(), ctx)
    assert isinstance(outcome, CheckmeOutcome)
    assert outcome.ok is True
    assert outcome.status == "VERIFIED"
    assert outcome.rounds == 1
    assert calls[0]["account"] == "acc1"
    assert calls[0]["stage"] == "join"
    assert calls[0]["round"] == 1
    assert calls[0]["peer_id"] == "@g1"
    assert len(outcome.trace_id) == 12


def test_wait_rounds_then_stop(ctx, monkeypatch):
    post, _ = _answers(
        _answer(checkme="WAIT", delay_ms=0),
        _answer(checkme="WAIT", delay_ms=0),
        _answer(checkme="STOP"),
    )
    outcome = _run(_config(), ctx, monkeypatch=monkeypatch, post=post)
    assert outcome.status == "VERIFIED"
    assert outcome.rounds == 3


def test_need_action_runs_actions_and_resubmits(ctx, monkeypatch):
    executor = Recorder()
    post, calls = _answers(
        _answer(
            checkme="NEED_ACTION",
            need_resubmit=True,
            actions=[
                {"action": "SEND_MESSAGE", "text": "hello"},
                {"action": "CLICK_BUTTON", "message_id": 7, "data_b64": ""},
            ],
        ),
        _answer(checkme="STOP"),
    )
    outcome = _run(
        _config(), ctx, executor=executor, monkeypatch=monkeypatch, post=post
    )
    assert outcome.ok is True
    assert outcome.actions_done == 2
    assert [item["action"] for item in executor.seen] == [
        "SEND_MESSAGE",
        "CLICK_BUTTON",
    ]
    assert calls[1]["round"] == 2


def test_a_failed_action_fails_the_chain(ctx, monkeypatch):
    executor = Recorder(fail_on="CLICK_BUTTON")
    post, _ = _answers(
        _answer(
            checkme="NEED_ACTION",
            need_resubmit=True,
            actions=[{"action": "CLICK_BUTTON", "message_id": 7}],
        ),
    )
    outcome = _run(
        _config(), ctx, executor=executor, monkeypatch=monkeypatch, post=post
    )
    assert outcome.ok is False
    assert outcome.status == "FAILED"
    assert outcome.reason == "action failed"


def test_max_round_gives_up_with_the_reference_line(ctx, capsys, monkeypatch):
    post, _ = _answers(_answer(checkme="WAIT", delay_ms=0))
    outcome = _run(_config(max_round=3), ctx, monkeypatch=monkeypatch, post=post)
    assert outcome.status == "GIVEUP"
    assert outcome.reason == "maxRound reached"
    assert "maxRound reached (3), give up." in capsys.readouterr().out


def test_an_expired_state_token_gives_up(ctx, capsys, monkeypatch):
    post, _ = _answers(
        _answer(checkme="WAIT", state_token="tok1", ttl_sec=0.0, delay_ms=0),
        _answer(checkme="STOP"),
    )
    outcome = _run(_config(), ctx, monkeypatch=monkeypatch, post=post)
    assert outcome.status == "GIVEUP"
    assert outcome.state_token == "tok1"
    assert "state_token expired, give up." in capsys.readouterr().out


def test_an_answer_without_the_checkme_key_is_unknown_but_not_fatal(ctx, monkeypatch):
    post, _ = _answers(
        _answer(reason="service restart"),
        _answer(checkme="STOP"),
    )
    outcome = _run(_config(), ctx, monkeypatch=monkeypatch, post=post)
    assert outcome.status == "VERIFIED"
    assert outcome.rounds == 2


def test_need_manual_finishes_without_ok(ctx, monkeypatch):
    post, _ = _answers(_answer(checkme="NEED_MANUAL", reason="captcha"))
    outcome = _run(_config(), ctx, monkeypatch=monkeypatch, post=post)
    assert outcome.ok is False
    assert outcome.status == "NEED_MANUAL"
    assert outcome.reason == "captcha"


def test_http_errors_log_the_failed_line_and_retry(ctx, capsys, monkeypatch):
    state = {"n": 0}

    def fake_post(url, payload, timeout):
        state["n"] += 1
        if state["n"] == 1:
            return 502, "bad gateway"
        return 200, _answer(checkme="STOP")

    outcome = _run(_config(), ctx, monkeypatch=monkeypatch, post=fake_post)
    assert outcome.status == "VERIFIED"
    out = capsys.readouterr().out
    assert "__checkme__ failed http=502" in out
    assert "__checkme__ status=STOP" in out


def test_the_finish_line_names_every_reference_field(ctx, capsys, monkeypatch):
    post, _ = _answers(_answer(checkme="STOP", state_token="tok9"))
    _run(_config(), ctx, monkeypatch=monkeypatch, post=post)
    out = capsys.readouterr().out
    assert "finish status=VERIFIED" in out
    assert "peer=@g1 join_source=batch_join task_id=42" in out
    assert "state_token=tok9" in out


def test_the_default_executor_reports_manual_only():
    done, detail = CheckmeExecutor().perform({"action": "SEND_MESSAGE"})
    assert done is False
    assert "has no executor in this build" in detail
    done, detail = CheckmeExecutor().perform({"action": "OPEN_URL"})
    assert done is False
    assert "manual-only in this build" in detail


def test_telegram_executor_send_message_uses_the_account_client(monkeypatch):
    sent: list[tuple[str, str]] = []

    class FakeSender:
        def client_for(self, account: str):
            class Client:
                def send_message(self, peer, text):
                    sent.append((peer, text))

            return Client()

    executor = TelegramExecutor(FakeSender(), "acc1", "@g1")
    done, detail = executor.perform({"action": "SEND_MESSAGE", "text": "hi there"})
    assert done is True
    assert sent == [("@g1", "hi there")]
    assert "SEND_MESSAGE sent" in detail


def test_the_config_chain_is_off_until_both_keys_are_set():
    assert CheckmeConfig().active is False
    assert CheckmeConfig(enabled=True).active is False
    assert CheckmeConfig(base_url="http://x").active is False
    assert CheckmeConfig(enabled=True, base_url="http://x").active is True


def test_batch_join_records_a_checkme_failure_against_the_pair(ctx, monkeypatch, store):
    """A live GIVEUP after a join lands as CHECKME_GIVEUP on the pair."""
    from types import SimpleNamespace

    from ogtool.features._common import open_task
    from ogtool.features.batch_join import FEATURE, JoinConfig
    from ogtool.features.checkme import CheckmeOutcome

    class ChainOn(CheckmeConfig):
        @property
        def active(self) -> bool:
            return True

    settings = SimpleNamespace(checkme=ChainOn(base_url="http://svc", enabled=True))
    live_ctx = SimpleNamespace(
        settings=settings,
        store=ctx.store,
        log=ctx.log,
        live=True,
        sender=lambda: SimpleNamespace(),
        waited=0.0,
    )

    def fake_verify_join(config, run_ctx, account, index, peer, task_id, executor=None):
        return CheckmeOutcome(
            ok=False,
            status="GIVEUP",
            reason="maxRound reached",
            rounds=3,
            actions_done=0,
        )

    import ogtool.features.batch_join as bj

    monkeypatch.setattr(bj, "verify_join", fake_verify_join, raising=False)
    monkeypatch.setattr("ogtool.features.checkme.verify_join", fake_verify_join)

    config = JoinConfig(account_labels=["acc1"])
    task = open_task(ctx, "checkme-run", FEATURE, {}, 1)
    job = SimpleNamespace(
        account="acc1",
        subject="3965371671",
        extra={"ok": "1", "code": "ok"},
    )
    bj.post_join_verify(config, live_ctx, task, job, ["acc1"])
    rows = store.results_for(task.id)
    assert rows and rows[-1]["error_code"] == "CHECKME_GIVEUP"
    assert "checkme: maxRound reached (3 round(s))" in rows[-1]["detail"]


def test_batch_join_webhook_still_works_when_checkme_is_off(ctx, monkeypatch, store):
    """No checkme keys: the bare webhook chain stays in charge."""
    from types import SimpleNamespace

    from ogtool.features._common import open_task
    from ogtool.features.batch_join import FEATURE, JoinConfig, post_join_verify

    settings = SimpleNamespace(checkme=CheckmeConfig())
    live_ctx = SimpleNamespace(
        settings=settings,
        store=ctx.store,
        log=ctx.log,
        live=True,
        sender=lambda: SimpleNamespace(),
        waited=0.0,
    )

    def fake_webhook(config, run_ctx, account, target):
        return False

    import ogtool.features.batch_join as bj

    monkeypatch.setattr(bj, "_verify_join", fake_webhook)
    config = JoinConfig(verify_url="http://hook")
    task = open_task(ctx, "webhook-run", FEATURE, {}, 1)
    job = SimpleNamespace(
        account="acc1",
        subject="3965371671",
        extra={"ok": "1", "code": "ok"},
    )
    post_join_verify(config, live_ctx, task, job, ["acc1"])
    rows = store.results_for(task.id)
    assert rows and rows[-1]["error_code"] == "VERIFY_FAILED"


def test_post_join_verify_is_silent_in_a_dry_run(ctx, store):
    """A dry run never contacts anything and never records a failure."""
    from types import SimpleNamespace

    from ogtool.features._common import open_task
    from ogtool.features.batch_join import FEATURE, JoinConfig, post_join_verify

    class ChainOn(CheckmeConfig):
        @property
        def active(self) -> bool:
            return True

    dry_ctx = SimpleNamespace(
        settings=SimpleNamespace(checkme=ChainOn(base_url="http://svc", enabled=True)),
        store=ctx.store,
        log=ctx.log,
        live=False,
        sender=lambda: SimpleNamespace(),
        waited=0.0,
    )
    config = JoinConfig(verify_url="http://hook")
    task = open_task(ctx, "dry-run", FEATURE, {}, 1)
    job = SimpleNamespace(
        account="acc1",
        subject="3965371671",
        extra={"ok": "1", "code": "ok"},
    )
    post_join_verify(config, dry_ctx, task, job, ["acc1"])
    assert store.results_for(task.id) == []
