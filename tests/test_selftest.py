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


def test_live_is_skipped_without_a_config(tmp_path):
    line, problems = selftest.check_live(base=tmp_path)
    assert problems == []
    assert "skipped" in line


def test_live_reports_a_missing_script(tmp_path):
    (tmp_path / "config.yaml").write_text("logs: l\n", encoding="utf-8")
    line, problems = selftest.check_live(base=tmp_path)
    assert problems
    assert "live_smoke" in " ".join(problems) or "Exit" in line


def test_a_stale_live_block_is_reported(tmp_path):
    """The generated real-machine table is checked exactly like the evidence one."""
    from ogtool import selftest

    note = tmp_path / "note.md"
    note.write_text(
        "<!-- current: pytest → 1 passed -->\n"
        f"{selftest.LIVE_START}\nold table\n{selftest.LIVE_END}\n",
        encoding="utf-8")
    _line, problems = selftest.check_docs(
        (note,), expected=1, block="")
    assert any("live block is stale" in item for item in problems), problems


def test_the_gate_check_names_a_refusal(monkeypatch):
    """The live check proves the gate refuses, without sending anything."""
    from pathlib import Path

    from ogtool import selftest as module

    here = Path(__file__).resolve().parents[1]
    # The real case file may name real chats; the gate probe uses its own.
    monkeypatch.setenv("OGTOOL_LIVE_CASES", str(here / "does-not-exist.yaml"))
    problems = module.gate_problems(here)
    assert problems == [], problems


def test_the_gate_check_also_proves_the_flag_runs_the_case(tmp_path):
    """Refusing is half of it; the flag must actually let the case through."""
    from pathlib import Path

    from ogtool import selftest as module

    here = Path(__file__).resolve().parents[1]
    cases = Path(tmp_path) / "one.yaml"
    cases.write_text("cases:\n  - name: gate-canary\n    args: [runs, list]\n"
                     "    write: true\n", encoding="utf-8")
    refused = module._smoke(here, cases, ["--only", "gate-canary"])
    assert refused.returncode == 2
    assert "refusing" in (refused.stdout + refused.stderr)
    allowed = module._smoke(here, cases,
                            ["--allow-write", "--only", "gate-canary"])
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert "gate-canary" in allowed.stdout


def test_the_docs_check_runs_before_the_live_one():
    """docs runs before live, so the receipts live writes make the table stale."""
    from ogtool import selftest as module

    # docs validates a table built from receipts, and the live check writes
    # receipts: the order is why a finished run warns that the note went stale.
    assert module.CHECKS.index("docs") < module.CHECKS.index("live")
