"""End to end through the CLI: accounts, then the three dry runs."""

from __future__ import annotations

from pathlib import Path

from ogtool.cli import run

CONFIG = """\
api_id: 0
api_hash: ''
session_dir: data/sessions
db: data/og.sqlite
logs: data/logs
"""


def setup_repo(tmp_path: Path) -> str:
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    session = tmp_path / "seed.session"
    session.write_bytes(b"")
    assert run(["--config", str(config), "accounts", "add",
                "--session", str(session), "--label", "acc1"]) == 0
    assert run(["--config", str(config), "accounts", "add",
                "--phone", "+8613800000000", "--label", "acc2"]) == 0
    return str(config)


def test_accounts_list_and_test(tmp_path, capsys):
    config = setup_repo(tmp_path)
    assert run(["--config", config, "accounts", "list"]) == 0
    output = capsys.readouterr().out
    assert "acc1" in output and "acc2" in output and "total: 2" in output

    # One of the two session files exists, so the summary is not all green:
    # the exit code has to say so, or automation cannot tell.
    assert run(["--config", config, "accounts", "test"]) == 1
    tested = capsys.readouterr().out
    assert "dry-run: session file present" in tested
    assert "ok: 1" in tested


def test_mass_send_dry_run(tmp_path, capsys):
    config = setup_repo(tmp_path)
    code = run([
        "--config", config, "mass_send",
        "--targets", "@demo_a,@demo_b,@demo_c",
        "--text", "hello",
        "--interval-min", "1", "--interval-max", "2",
        "--threads", "2",
    ])
    output = capsys.readouterr().out
    assert code == 0
    assert output.count("account=acc") == 3
    assert "PLAN: 3 messages, 2 accounts" in output


def test_the_resume_switch_can_be_turned_off_from_the_cli(tmp_path, capsys):
    # The pair used to be declared as one option string, so argparse wanted a
    # value for a switch and --no-resume did not exist at all.
    config = setup_repo(tmp_path)
    code = run([
        "--config", config, "mass_send_v2",
        "--targets", "@demo_a", "--text", "hello", "--no-resume",
        "--daily-limit", "99",
    ])
    output = capsys.readouterr().out
    assert code == 0
    assert "resumed: 0" in output


def test_keep_online_dry_run(tmp_path, capsys):
    config = setup_repo(tmp_path)
    assert run(["--config", config, "keep_online", "--interval", "60",
                "--loops", "1"]) == 0
    output = capsys.readouterr().out
    assert "PLAN: 2 heartbeats, 2 accounts" in output


def test_auto_reply_dry_run(tmp_path, capsys):
    config = setup_repo(tmp_path)
    assert run(["--config", config, "auto_reply",
                "--rule", "天气=>今天不错",
                "--simulate", "@demo|alice|今天天气不错",
                "--simulate", "@demo|bob|别的话题"]) == 0
    output = capsys.readouterr().out
    assert "OUT -> rule1" in output
    assert "SKIP: no rule matched" in output
    assert "PLAN: 1 replies" in output


def test_task_file_is_merged(tmp_path, capsys):
    config = setup_repo(tmp_path)
    task = tmp_path / "task.yaml"
    task.write_text(
        "task_name: from-file\n"
        "targets:\n  inline: '@x,@y'\n"
        "content:\n  text: from task\n",
        encoding="utf-8",
    )
    assert run(["--config", config, "mass_send", "--task", str(task)]) == 0
    output = capsys.readouterr().out
    assert "task: from-file" in output
    assert "PLAN: 2 messages" in output


def test_missing_config_reports_cleanly(tmp_path, capsys):
    assert run(["--config", str(tmp_path / "nope.yaml"), "keep_online"]) == 2
    assert "not found" in capsys.readouterr().out
