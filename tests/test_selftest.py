"""``ogtool selftest``: the hand-run checks, with an exit code."""

from __future__ import annotations

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
