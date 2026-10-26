"""Stage 4 of the workflow is a PULL, not an invite.

The reference numbers it: "4) Batch Pull v2 (pull own contacts)" then
"5) Mass Send DM v2 (to own contacts)". So the pull reads the account's own
address book and the DM stage targets what it found.
"""

from __future__ import annotations

from ogtool.features import mix_market
from ogtool.features.mix_market import MixConfig, stage_pull

_TASK = type("T", (), {"id": 1})()


def _config(contacts=None) -> MixConfig:
    return MixConfig(contacts=list(contacts or []), group_titles=["A"])


class _PullingSender:
    def __init__(self, rows):
        self.rows = rows
        self.seen = []

    def pull_contacts(self, account):
        self.seen.append(account)
        return type("R", (), {"ok": True, "code": "", "detail": self.rows.get(
            account, "")})()


class _Store:
    """The only two things ``record`` asks of a store."""

    def __init__(self):
        self.rows = []

    def add_result(self, task_id, account, subject, status, code, detail):
        self.rows.append((account, subject, status))


class _Ctx:
    """Just enough of a RunContext for the stage: a sender and a store."""

    live = False
    task_id = None

    def __init__(self, sender):
        self._sender = sender
        self.store = _Store()

    def sender(self):
        return self._sender

    def note(self, *args, **kwargs):
        return None


def test_the_pull_collects_contacts_from_every_account(ctx):
    sender = _PullingSender({"acc1": "Ann|@ann\nBob|@bob", "acc2": "Cid|@cid"})
    config = _config()
    owners: dict[str, str] = {}
    result = stage_pull(config, _Ctx(sender), _TASK, ["acc1", "acc2"], owners)
    assert sender.seen == ["acc1", "acc2"]
    assert result.ok == 3
    assert config.contacts == ["Ann|@ann", "Bob|@bob", "Cid|@cid"]
    assert owners == {"@ann": "acc1", "@bob": "acc1", "@cid": "acc2"}


def test_each_account_keeps_its_own_contacts(ctx):
    sender = _PullingSender({"acc1": "Ann|@ann", "acc2": "Bob|@bob"})
    config = _config()
    owners: dict[str, str] = {}
    stage_pull(config, _Ctx(sender), _TASK, ["acc1", "acc2"], owners)
    assert owners["@ann"] == "acc1"
    assert owners["@bob"] == "acc2"


def test_a_failed_pull_is_counted_not_swallowed(ctx, capsys):
    class Failing:
        def pull_contacts(self, account):
            return type("R", (), {"ok": False, "code": "FLOOD_WAIT_30",
                                  "detail": ""})()

    config = _config(["Ann|@ann"])
    result = stage_pull(config, _Ctx(Failing()), _TASK, ["acc1"], {})
    assert result.failed == 1
    assert "failed FLOOD_WAIT_30" in capsys.readouterr().out


def test_an_empty_pull_falls_back_to_the_configured_contacts(ctx, capsys):
    class Empty:
        def pull_contacts(self, account):
            return type("R", (), {"ok": True, "code": "", "detail": ""})()

    config = _config(["Ann|@ann"])
    stage_pull(config, _Ctx(Empty()), _TASK, ["acc1"], {})
    assert config.contacts == ["Ann|@ann"]
    assert "the configured contacts stand in" in capsys.readouterr().out


def test_the_stage_is_registered_under_the_reference_name():
    assert mix_market.STAGES[3] == ("batch_pull", "pull own contacts")


def test_the_pull_does_not_join_anything(ctx):
    """An invite would call ``invite``; the pull never does."""

    class Watched:
        def __init__(self):
            self.invited = 0

        def pull_contacts(self, account):
            return type("R", (), {"ok": True, "code": "", "detail": ""})()

        def invite(self, *args, **kwargs):
            self.invited += 1
            return type("R", (), {"ok": True, "code": "", "detail": ""})()

    sender = Watched()
    stage_pull(_config(), _Ctx(sender), _TASK, ["acc1"], {})
    assert sender.invited == 0


def test_a_failed_group_is_held_then_retried_once(ctx, capsys):
    """``heldTargets`` + "Retry after %1 minute(s)" (1741600).

    A creation failure is usually a flood reply that has expired by the time the
    rest is over, so the title is retried once instead of being dropped - which
    would silently lose a whole target.
    """
    from ogtool.features._common import open_task
    from ogtool.features.mix_market import MixConfig, stage_create_groups

    ctx.script.codes = ["FLOOD_WAIT_30", "ok"]
    task = open_task(ctx, "retry test", "mix_market", {"stage": 2}, 1)
    config = MixConfig(group_titles=["Market A"], create_rest_min=1,
                       create_rest_max=1)
    result = stage_create_groups(config, ctx, task, ["acc1"])
    output = capsys.readouterr().out
    assert "Retry after 1 minute(s)" in output
    assert result.ok == 1
    assert result.failed == 0
    assert result.held == []


def test_a_second_failure_keeps_the_target_held(ctx, capsys):
    from ogtool.features._common import open_task
    from ogtool.features.mix_market import MixConfig, stage_create_groups

    ctx.script.codes = ["FLOOD_WAIT_30", "FLOOD_WAIT_30"]
    task = open_task(ctx, "held test", "mix_market", {"stage": 2}, 1)
    config = MixConfig(group_titles=["Market A"], create_rest_min=1,
                       create_rest_max=1)
    result = stage_create_groups(config, ctx, task, ["acc1"])
    assert result.held == ["Market A"]
    assert "failed again" in capsys.readouterr().out
