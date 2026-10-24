"""Shared fixtures: a throwaway config, database and account pool."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ogtool.config import Settings
from ogtool.runtime import RunContext
from ogtool.simulate import Script
from ogtool.store import Account, Store

CONFIG = """\
api_id: 0
api_hash: ''
session_dir: data/sessions
db: data/og.sqlite
logs: data/logs
ai:
  base_url: ''
  api_key: ''
  model: ''
"""


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    return Settings.load(config)


@pytest.fixture
def store(settings: Settings):
    database = Store(settings.db_path)
    yield database
    database.close()


@pytest.fixture
def pooled(settings: Settings, store: Store) -> list[str]:
    labels = ["acc1", "acc2"]
    for label in labels:
        session = settings.session_path(label)
        session.write_bytes(b"")
        store.add_account(Account(label, session=str(session)))
    return labels


@pytest.fixture
def scripted(ctx: RunContext):
    """Feed the dry-run sender a list of answers, in order."""

    def _make(codes: list[str]) -> RunContext:
        ctx.script = Script(codes)
        ctx.close()
        ctx._sender = None
        return ctx

    return _make


@pytest.fixture
def live(ctx: RunContext):
    """Flip a context to live with a stand-in transport attached."""

    def _make(sender: object) -> RunContext:
        ctx.live = True
        ctx._sender = sender
        return ctx

    return _make


@pytest.fixture
def session(settings: Settings):
    """An empty session file, so the live backend gets past its existence check."""
    path = settings.session_path("t1")
    path.write_bytes(b"")
    return path


@pytest.fixture
def credentials(monkeypatch):
    """The api id/hash blob the live backend reads from the secret store."""
    monkeypatch.setenv("DSH_SECRET_TG_API_CREDENTIALS",
                       "api_id 1234567 api_hash " + "ca" * 16)


@pytest.fixture
def spec_client() -> MagicMock:
    """A client stand-in with exactly the methods ``TelegramClient`` has.

    ``spec`` is the whole point: a made up ``client.join`` raises here the way
    it raises on a real session.  A bare mock answers it instead, which is how
    six nonexistent methods kept 386 tests green.
    """
    from telethon.sync import TelegramClient

    return MagicMock(spec=TelegramClient)


class OkTransport:
    """A transport that accepts every call, recording the operation names.

    The pacing tests only care that the page asked, waited and moved on; the
    answer is always the same, so ``calls`` is the whole record of what ran.
    """

    live = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args: object, **kwargs: object):
            from ogtool.senders import SendResult

            self.calls.append(name)
            return SendResult(ok=True)

        return call


@pytest.fixture
def ok_sender() -> OkTransport:
    return OkTransport()


@pytest.fixture
def slept(monkeypatch) -> list[float]:
    """Record every pause a page takes instead of waiting for it.

    ``time.sleep`` is patched on the module object every feature shares, so it
    covers ``_common.pause`` and the backoff sleeps in the runner alike.
    """
    from ogtool.features import _common

    seen: list[float] = []
    monkeypatch.setattr(_common.time, "sleep", seen.append)
    return seen


@pytest.fixture
def ctx(settings: Settings, store: Store, pooled: list[str]) -> RunContext:
    logger = logging.getLogger("ogtool.test")
    logger.addHandler(logging.NullHandler())
    context = RunContext(settings=settings, store=store, log=logger, live=False)
    yield context
    context.close()


DEMO_PATTERN = [
    "# header comment",
    "A4|L0|D6|[STICKER:set_a/happy.webp]",
    "-A5|L0|D21|[IMG:pic1.jpg]",
    "--A15|L0|D22|",
    "A25|L0|D39|hello there",
]


@pytest.fixture
def pattern_file(tmp_path: Path):
    """A small chat_pattern script on disk."""

    def _make(lines: list[str] | None = None, name: str = "demo.txt") -> str:
        path = tmp_path / name
        path.write_text("\n".join(lines or DEMO_PATTERN) + "\n",
                        encoding="utf-8")
        return str(path)

    return _make


@pytest.fixture
def fake_ai(settings: Settings):
    """A model client whose transport the test controls."""
    from ogtool.ai import AIClient

    def _make(transport):
        return AIClient(settings.ai, transport=transport)

    return _make
