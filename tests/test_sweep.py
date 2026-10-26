"""The sweep is evidence, so it is tested like one.

It runs real subprocesses, which is the point: a page that only works when called
in-process is a page that does not work.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _sweep():
    spec = importlib.util.spec_from_file_location("og_sweep",
                                                  ROOT / "scripts/sweep.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sweep():
    return _sweep()


def test_two_runs_of_one_fixture_leave_the_same_receipt(sweep):
    """Determinism is the claim: same config, same receipt."""
    from ogtool.receipts import diff

    table = sweep.fixtures()
    first_code, first_out, first = sweep.dry_run("batch_rename", table)
    second_code, _second_out, second = sweep.dry_run("batch_rename", table)
    assert (first_code, second_code) == (0, 0), first_out
    assert len(first) == 1 and len(second) == 1
    assert diff(first[0], second[0]) == []


def test_a_randomised_page_is_reproducible_once_seeded(sweep):
    """ai_comment draws its plan, so its own seed is part of the config."""
    from ogtool.receipts import diff

    table = sweep.fixtures()
    _code, _out, first = sweep.dry_run("ai_comment", table)
    _code2, _out2, second = sweep.dry_run("ai_comment", table)
    assert first and second
    assert first[0]["knobs"]["seed"] == 1
    assert diff(first[0], second[0]) == []


def test_each_run_gets_its_own_receipt_directory(sweep):
    """A leftover receipt must not be able to satisfy the next run's check."""
    table = sweep.fixtures()
    _code, _out, first = sweep.dry_run("proxy", table)
    _code2, _out2, second = sweep.dry_run("proxy", table)
    assert len(first) == 1 and len(second) == 1
    assert first[0]["task_id"] == second[0]["task_id"] == 0


def test_the_scratch_config_binds_an_account(sweep):
    """A page with no account plans nothing, and an empty receipt proves it."""
    from ogtool.config import Settings
    from ogtool.store import Store

    config, _receipts = sweep.scratch_config()
    store = Store(Settings.load(config).db_path)
    try:
        labels = [account.label for account in store.list_accounts()]
    finally:
        store.close()
    assert labels == ["sweep"]


def test_a_planned_row_is_counted_as_ok(sweep):
    """mass_send closed a dry run with ok=0 while its rows said 1.

    The sweep could not see it because the scratch pool was empty: the plan was
    empty too, so the counts agreed by both being nothing.
    """
    table = sweep.fixtures()
    code, out, bodies = sweep.dry_run("mass_send", table)
    assert code == 0, out
    body = bodies[0]
    assert body["planned"] == 1
    assert body["ok"] == 1, body["results"]
    assert body["unaccounted"] == 0
