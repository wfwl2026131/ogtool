"""``ogtool selftest``: the hand-run checks, with an exit code."""

from __future__ import annotations

import pytest

from ogtool import cli, receipts, selftest


def _config(settings) -> str:
    return str(settings.path)


def test_receipts_check_passes_on_a_clean_directory(settings, capsys):
    receipts.write_run(settings, "settings", exit_code=0, live=False)
    assert cli.run(["--config", _config(settings), "selftest",
                    "--only", "receipts"]) == 0
    output = capsys.readouterr().out
    assert "receipts: 1 receipt(s), 0 problem(s)" in output
    assert "selftest: 1 check(s), 0 problem(s)" in output


def test_receipts_check_fails_on_a_broken_receipt(settings, capsys):
    (receipts.receipt_dir(settings) / "broken.json").parent.mkdir(
        parents=True, exist_ok=True)
    (receipts.receipt_dir(settings) / "broken.json").write_text(
        '{"version": 1}', encoding="utf-8")
    assert cli.run(["--config", _config(settings), "selftest",
                    "--only", "receipts"]) == 1
    output = capsys.readouterr().out
    assert "missing feature" in output
    assert "selftest: 1 check(s)," in output


def test_the_knob_check_runs_the_real_audit(capsys):
    assert cli.run(["--config", "config.example.yaml", "selftest",
                    "--only", "knobs"]) == 0
    output = capsys.readouterr().out
    assert "knobs: dead:" in output
    assert "undecided: 0" in output


def test_an_unknown_check_is_refused(settings, capsys):
    assert cli.run(["--config", _config(settings), "selftest",
                    "--only", "nope"]) == 2
    assert "unknown check nope" in capsys.readouterr().out


def test_a_missing_sweep_script_is_a_problem(tmp_path, settings, capsys):
    line, problems = selftest.check_sweep(base=tmp_path)
    assert "missing" in line
    assert problems


def test_the_receipt_check_names_every_bad_field(settings):
    body = receipts.receipt_dir(settings)
    body.mkdir(parents=True, exist_ok=True)
    (body / "bad.json").write_text('{"version": 1, "planned": -3}',
                                   encoding="utf-8")
    line, problems = selftest.check_receipts(settings)
    assert "1 receipt(s)" in line
    assert any("planned is negative" in item for item in problems)
    assert any("missing feature" in item for item in problems)


def test_no_check_at_all_still_reports(settings, capsys):
    assert selftest.run(settings, only=["receipts"]) == 0
    assert "selftest: 1 check(s), 0 problem(s)" in capsys.readouterr().out


def test_docs_check_passes_when_the_claim_matches(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("# note\n\n<!-- current: pytest → 42 passed -->\n",
                    encoding="utf-8")
    line, problems = selftest.check_docs(paths=(note,), expected=42)
    assert problems == []
    assert "1 note(s) checked against 42" in line


def test_docs_check_catches_a_stale_number(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("# note\n\n<!-- current: pytest → 41 passed -->\n",
                    encoding="utf-8")
    _line, problems = selftest.check_docs(paths=(note,), expected=42)
    assert any("claims 41 passed, pytest collects 42" in item
               for item in problems)


def test_docs_check_wants_the_marker(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("# note\nno marker here\n", encoding="utf-8")
    _line, problems = selftest.check_docs(paths=(note,), expected=1)
    assert any("no '<!-- current: pytest -> N -->' marker" in item
               for item in problems)


def test_docs_check_skips_a_note_that_is_not_on_this_machine(tmp_path, capsys):
    line, problems = selftest.check_docs(
        paths=(tmp_path / "gone.md",), expected=1)
    assert problems == []
    assert "0 note(s) checked" in line
    assert "not on this machine" in capsys.readouterr().out


def test_the_real_checkout_has_no_stale_test_count():
    """The two project notes must agree with the tests this tree collects."""
    line, problems = selftest.check_docs()
    if "0 note(s) checked" in line:
        pytest.skip("notes are not on this machine")
    assert problems == []
