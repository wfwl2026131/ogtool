"""The frozen check goes through @spambot, exactly as the reference does it.

The dump puts the bot and the command side by side (lines 1742586-2592): the
``@spambot`` chat and ``/start``, inside a routine that says "Collecting frozen
accounts" and "Processing frozen account #%1 (index %2, phone %3)". So the probe
sends ``/start`` and reads the answer.

The answer's wording is Telegram's, not the reference's, so classification is
marked 【推测】: the reply text is printed first and the verdict is a convenience
on top of it.
"""

from __future__ import annotations

import pytest

from ogtool.accounts import AccountPool
from ogtool.spambot import (
    FREE_MARKERS,
    LIMITED_MARKERS,
    SPAMBOT,
    START,
    classify,
    headline,
    shorten,
)

FREE = ("Good news, no limits are currently applied to your account. "
        "You're free as a bird!")
HARSH = ("Unfortunately, some phone numbers may trigger a harsh response from "
         "our anti-spam systems. If you think this is a mistake, you can "
         "submit an appeal.")
LIMITED = ("I'm very sorry that you had to contact me, but unfortunately your "
           "account is now limited until 12 Nov 2026.")


def test_the_bot_and_the_command_are_the_dump_s_own():
    assert SPAMBOT == "spambot"
    assert START == "/start"


def test_a_free_account_reads_as_two_way():
    assert classify(FREE) == ("two_way", FREE_MARKERS[0])


def test_the_harsh_response_is_the_middle_state():
    assert classify(HARSH)[0] == "one_way"


def test_a_limited_account_reads_as_frozen():
    assert classify(LIMITED) == ("frozen", LIMITED_MARKERS[0])


def test_an_unfamiliar_reply_stays_unclassified():
    assert classify("beep boop") == ("", "")
    assert classify("") == ("", "")


def test_the_verdicts_have_one_line_each():
    assert "healthy" in headline("two_way")
    assert "one-way" in headline("one_way")
    assert "frozen" in headline("frozen")
    assert "unknown" in headline("")


def test_the_reply_is_shown_but_cut():
    assert shorten("a\nb  c") == "a b c"
    assert shorten("x" * 500).endswith("…")
    assert len(shorten("x" * 500)) == 201


class _FakeSpamBot:
    def __init__(self, reply: str):
        self.reply = reply
        self.sent: list[tuple[str, str]] = []
        self.disconnected = False

    def connect(self) -> None:
        return None

    def send_message(self, username: str, text: str) -> None:
        self.sent.append((username, text))

    def get_messages(self, username: str, limit: int = 5):
        return [type("M", (), {"message": self.reply})()]

    def disconnect(self) -> None:
        self.disconnected = True


@pytest.fixture
def pool(settings, store, monkeypatch):
    # The probe needs credentials to open a client; the values are fake, only
    # the shape matters, and nothing here reaches the network.
    monkeypatch.setenv("TG_API_ID", "12345")
    monkeypatch.setenv("TG_API_HASH", "0123456789abcdef0123456789abcdef")
    return AccountPool(settings, store)


def _seed(pool, label="acc1"):
    from pathlib import Path as _Path

    pool.add_phone("+8613800000001", label)
    # The pool only records the path; the probe refuses an account whose
    # session file is not actually there, so the fixture creates it.
    _Path(pool.resolve([label])[0].session).touch()
    return label


def test_a_dry_run_sends_nothing_and_says_what_it_would_send(pool):
    _seed(pool)
    rows = pool.spambot(None, live=False)
    label, kind, ok, detail = rows[0]
    assert (label, kind, ok) == ("acc1", "", True)
    assert f"would send {START} to @{SPAMBOT}" in detail


def test_a_live_probe_sends_start_and_classifies(pool):
    _seed(pool)
    fake = _FakeSpamBot(FREE)
    rows = pool.spambot(None, live=True, wait=0,
                        client_factory=lambda _p, _c: fake)
    label, kind, ok, detail = rows[0]
    assert fake.sent == [(SPAMBOT, START)]
    assert (label, kind, ok) == ("acc1", "two_way", True)
    assert "healthy" in detail
    assert "reply=Good news" in detail
    assert fake.disconnected is True


def test_the_verdict_is_written_only_when_asked(pool, store):
    _seed(pool)
    fake = _FakeSpamBot(LIMITED)
    pool.spambot(None, live=True, wait=0,
                 client_factory=lambda _p, _c: fake)
    assert store.get_account("acc1").kind in ("", None)
    pool.spambot(None, live=True, wait=0, apply=True,
                 client_factory=lambda _p, _c: _FakeSpamBot(LIMITED))
    assert store.get_account("acc1").kind == "frozen"


def test_an_unclassified_reply_leaves_the_kind_alone(pool, store):
    _seed(pool)
    pool.spambot(None, live=True, wait=0, apply=True,
                 client_factory=lambda _p, _c: _FakeSpamBot("beep boop"))
    assert store.get_account("acc1").kind in ("", None)


def test_a_missing_session_is_a_row_not_an_exception(pool):
    pool.add_phone("+8613800000002", "ghost")
    rows = pool.spambot(["ghost"], live=True, wait=0)
    label, kind, ok, detail = rows[0]
    assert ok is False and "SESSION_NOT_FOUND" in detail
