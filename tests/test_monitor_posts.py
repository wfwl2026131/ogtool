"""The monitor lane works on messages (G/M), not only on the member roster.

The reference dispatches "Monitor mode: dispatched G/M:%1 to inviter #%2"
(:1743480) and skips "hidden user G/M:%1 Name:%2" (:1743467).  A sender
that can read history drives that lane; a sender that only lists members
still takes the old roster path.
"""

from __future__ import annotations

import itertools
from types import SimpleNamespace

from ogtool.features.batch_invite_v2 import InviteV2Config, _monitor


def _result(posts, origins=None, senders=None, names=None):
    return SimpleNamespace(
        ok=True,
        code="OK",
        posts=posts,
        post_origins=origins or {},
        post_senders=senders or {},
        post_names=names or {},
    )


class _Sender:
    """A sender that reads history and records the invites it is asked for."""

    def __init__(self, results):
        self.results = itertools.cycle(results)
        self.invites = []

    def history(self, account, target, minutes=0, limit=0):
        return next(self.results)

    def invite(self, handler, target, member):
        self.invites.append((handler, target.ref, member.ref))
        return SimpleNamespace(ok=True, code="ok")


def _drive(sender, monkeypatch, pauses_to_stop=6):
    import ogtool.features.batch_invite_v2 as module

    calls = itertools.count(1)

    def fake_pause(*a, **k):
        if next(calls) >= pauses_to_stop:
            raise KeyboardInterrupt()

    monkeypatch.setattr(module, "pause", fake_pause)
    monkeypatch.setattr(module.time, "monotonic", lambda: 0.0)

    ctx = SimpleNamespace(
        live=True,
        bind=lambda labels: [SimpleNamespace(label=label) for label in labels],
        sender=lambda: sender,
    )
    config = InviteV2Config(
        account_labels=["acc1"],
        monitor_group="https://t.me/c/3965371671",
        targets=["https://t.me/c/3965371671"],
        monitor_skip_existing=True,
    )
    return _monitor(config, ctx)


def test_the_baseline_skips_posts_that_were_already_there(ctx, capsys, monkeypatch):
    sender = _Sender(
        [_result([(7, "hi")], senders={7: "@alice"}, names={7: "Alice A."})]
    )
    assert _drive(sender, monkeypatch) == 130
    out = capsys.readouterr().out
    assert "1 existing post(s) baseline" in out
    assert sender.invites == []


def test_a_forwarded_posts_original_writer_is_the_invited_candidate(
    ctx, capsys, monkeypatch
):
    sender = _Sender(
        [
            _result([], senders={}, names={}),  # baseline: nothing to skip
            _result(
                [(9, "forwarded")],
                origins={9: "@alice"},
                senders={9: "@bob"},
                names={9: "Alice A."},
            ),
            _result(
                [(9, "forwarded")],
                origins={9: "@alice"},
                senders={9: "@bob"},
                names={9: "Alice A."},
            ),
        ]
    )
    assert _drive(sender, monkeypatch) == 0
    out = capsys.readouterr().out
    assert "Monitor mode: dispatched G/M:3965371671/9" in out
    assert sender.invites and sender.invites[0][2] == "@alice"


def test_a_hidden_author_is_skipped_with_the_references_own_line(
    ctx, capsys, monkeypatch
):
    sender = _Sender(
        [
            _result([], senders={}, names={}),
            _result([(9, "hi")], senders={9: "@ghost"}, names={9: "Hidden User"}),
            _result([(9, "hi")], senders={9: "@ghost"}, names={9: "Hidden User"}),
        ]
    )
    assert _drive(sender, monkeypatch) == 130
    out = capsys.readouterr().out
    assert ("Monitor mode: skip hidden user G/M:3965371671/9 Name:Hidden User") in out
    assert "current candidate skipped" in out
    assert sender.invites == []


def test_a_post_with_no_sender_has_no_eligible_target(ctx, capsys, monkeypatch):
    sender = _Sender(
        [
            _result([], senders={}, names={}),
            _result([(9, "channel post")], names={9: "Some Channel"}),
            _result([(9, "channel post")], names={9: "Some Channel"}),
        ]
    )
    assert _drive(sender, monkeypatch) == 130
    out = capsys.readouterr().out
    assert "no eligible target" in out
    assert sender.invites == []


def test_the_roster_lane_still_works_without_history(ctx, capsys, monkeypatch):
    """A sender with only get_participants keeps the old behaviour."""
    import ogtool.features.batch_invite_v2 as module

    class Roster:
        def __init__(self):
            self.invites = []
            self.rounds = iter(
                [
                    ["@old1"],
                    ["@old1", "@new1"],
                    ["@old1", "@new1"],
                ]
            )

        def get_participants(self, account, target, limit=0):
            return SimpleNamespace(ok=True, code="OK", items=next(self.rounds))

        def invite(self, handler, target, member):
            self.invites.append(member.ref)
            return SimpleNamespace(ok=True, code="ok")

    roster = Roster()
    stops = iter([False, False, False, KeyboardInterrupt()])

    def fake_pause(*a, **k):
        if next(stops):
            raise KeyboardInterrupt()

    monkeypatch.setattr(module, "pause", fake_pause)
    monkeypatch.setattr(module.time, "monotonic", lambda: 0.0)

    ctx = SimpleNamespace(
        live=True,
        bind=lambda labels: [SimpleNamespace(label=label) for label in labels],
        sender=lambda: roster,
    )
    config = InviteV2Config(
        account_labels=["acc1"],
        monitor_group="https://t.me/c/3965371671",
        targets=["https://t.me/c/3965371671"],
        monitor_skip_existing=True,
    )
    assert _monitor(config, ctx) == 0
    assert "@new1" in roster.invites
