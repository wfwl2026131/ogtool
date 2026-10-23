"""Shared fixtures: a throwaway config, database and account pool."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ogtool.config import Settings
from ogtool.runtime import RunContext
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
def ctx(settings: Settings, store: Store, pooled: list[str]) -> RunContext:
    logger = logging.getLogger("ogtool.test")
    logger.addHandler(logging.NullHandler())
    context = RunContext(settings=settings, store=store, log=logger, live=False)
    yield context
    context.close()
