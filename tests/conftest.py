"""Shared fixtures: a throwaway config, database and account pool."""

from __future__ import annotations

import logging
from pathlib import Path

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
