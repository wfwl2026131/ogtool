"""The CLI's own write gate.

The smoke suite had one; the tool itself did not, so ``--live`` on a sending page
was enough to message strangers. These tests are about the sentence a person sees
and the exit code that follows it.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ogtool import cli
from ogtool.features import WRITE_FEATURES, load_defaults, names


def run(argv, settings_file, capsys):
    code = cli.run(["--config", str(settings_file), *argv])
    return code, capsys.readouterr().out


@pytest.fixture()
def config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("logs: logs\n", encoding="utf-8")
    (tmp_path / "logs").mkdir()
    return path


def test_a_write_page_is_refused_without_the_flag(config, capsys):
    code, out = run(
        ["mass_send", "--live", "--targets", "@BotFather", "--text", "hi"],
        config,
        capsys,
    )
    assert code == 2
    assert "mass_send writes into Telegram" in out
    assert "--allow-write" in out


def test_the_refusal_happens_before_a_task_is_opened(config, capsys):
    code, _ = run(["batch_invite", "--live"], config, capsys)
    assert code == 2
    receipts = config.parent / "logs" / "receipts"
    assert not receipts.exists() or not list(receipts.glob("batch_invite-*.json"))


def test_a_dry_run_is_never_refused(config, capsys):
    code, out = run(
        ["mass_send", "--dry-run", "--targets", "@BotFather", "--text", "hi"],
        config,
        capsys,
    )
    assert code == 0, out
    assert "PLAN:" in out


def test_the_flag_lets_the_plan_through(config, capsys):
    """With the flag the page runs; a dry run keeps it from sending anything."""
    code, out = run(
        [
            "mass_send",
            "--dry-run",
            "--allow-write",
            "--targets",
            "@BotFather",
            "--text",
            "hi",
        ],
        config,
        capsys,
    )
    assert code == 0, out


@pytest.mark.parametrize("feature", sorted(WRITE_FEATURES))
def test_every_write_page_is_gated(feature, config, capsys):
    """The set is the contract: a page in it can never run live ungated."""
    code, out = run([feature, "--live"], config, capsys)
    assert code == 2, f"{feature} ran live without --allow-write:\n{out}"
    assert "--allow-write" in out


def test_the_set_only_names_real_pages():
    load_defaults()
    assert set(names()) >= WRITE_FEATURES, (
        f"not features: {sorted(WRITE_FEATURES - set(names()))}"
    )
    # A read-only page must not be in it, or the gate would train people to add
    # the flag to everything and stop reading the message.
    assert (
        not {
            "filter_phone",
            "filter_group",
            "export_accounts",
            "keep_online",
            "proxy",
            "settings",
        }
        & WRITE_FEATURES
    )


@pytest.mark.parametrize("action", ["logout", "password", "spambot", "passkey"])
def test_the_write_account_actions_are_gated(action, config, capsys):
    code, out = run(["accounts", action, "--live", "--accounts", "t1"], config, capsys)
    assert code == 2
    assert f"accounts {action} writes into" in out


def test_reading_a_dialog_list_is_not_gated():
    """--live on a read page reaches the page, not the refusal."""
    args = cli.build_parser().parse_args(
        ["accounts", "dialogs", "--live", "--accounts", "t1"]
    )
    assert cli._write_refusal(args) == ""


def test_bot_links_is_gated_too():
    args = cli.build_parser().parse_args(["bot_links", "--live"])
    assert "--allow-write" in cli._write_refusal(args)


def _cli(argv, config):
    """Run the real entrypoint in a child process: argparse order is the point."""
    return subprocess.run(  # noqa: S603 - our own CLI, fixed arguments
        [sys.executable, "-m", "ogtool", "--config", str(config), *argv],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=str(config.parent),
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
    )


def test_every_write_page_refuses_at_the_process_level(config):
    """Function-level tests can miss a page that argparse never reaches.

    The gate runs after ``parse_args``, so a page whose flags are validated first
    would exit 2 for the wrong reason - and the test that only looked at the exit
    code would call that a pass. Here the sentence itself has to be in the
    output, for every page in the set, through the real command line.
    """
    load_defaults()
    wrong = []
    for name in sorted(WRITE_FEATURES):
        result = _cli([name, "--live"], config)
        text = result.stdout + result.stderr
        if result.returncode != 2 or f"ERROR: {name} writes into Telegram" not in text:
            wrong.append(f"{name}: rc={result.returncode} {text.strip()[:120]}")
    assert wrong == [], "\n".join(wrong)


def test_no_write_page_is_refused_when_it_only_plans(config):
    """A plan writes nothing, so the gate must not fire on --dry-run."""
    load_defaults()
    wrong = []
    for name in sorted(WRITE_FEATURES):
        result = _cli([name, "--dry-run"], config)
        text = result.stdout + result.stderr
        if "writes into Telegram; add --allow-write" in text:
            wrong.append(f"{name}: refused a dry run")
    assert wrong == [], "\n".join(wrong)
