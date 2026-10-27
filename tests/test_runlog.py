"""The run transcript: every command's console output lands on disk."""

from __future__ import annotations

import io
import threading
import time
from pathlib import Path

from ogtool import runlog


class Settings:
    log_dir = Path("logs")


def _settings(tmp_path: Path) -> Settings:
    out = Settings()
    out.log_dir = tmp_path
    return out


def test_every_command_leaves_a_transcript(tmp_path, monkeypatch, capsys):
    import subprocess
    import sys

    monkeypatch.chdir(tmp_path)
    example = Path(__file__).resolve().parents[1] / "config.example.yaml"
    (tmp_path / "config.yaml").write_text(example.read_text())
    done = subprocess.run(
        [sys.executable, "-m", "ogtool", "settings", "--json"],
        capture_output=True, text=True,
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[1]),
             "PATH": "/usr/bin:/bin:/usr/local/bin"})
    assert done.returncode == 0, done.stderr
    files = list((tmp_path / "data" / "logs" / "runs").glob("*.log"))
    assert files, "settings ran but left no transcript"
    body = files[0].read_text(encoding="utf-8")
    assert "restart_timed" in body, "console output missing from transcript"
    assert body.splitlines()[0][:2].isdigit(), "lines are not time-stamped"


def test_the_tee_keeps_the_terminal_exact_and_the_file_stamped(tmp_path):
    terminal = io.StringIO()
    transcript = io.StringIO()
    tee = runlog.Tee(terminal, transcript)
    tee.write("plain line\n")
    tee.write("no newline yet")
    assert terminal.getvalue() == "plain line\n"      # partial line held
    lines = transcript.getvalue().splitlines()
    assert lines[0].startswith(("0", "1", "2")) and "plain line" in lines[0]
    tee.close_run()
    assert terminal.getvalue() == "plain line\nno newline yet\n"
    lines = transcript.getvalue().splitlines()
    assert len(lines) == 2 and lines[1].endswith("no newline yet")


def test_tail_reads_the_last_lines(tmp_path):
    path = tmp_path / "runs" / "x.log"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(f"line{i}" for i in range(80)) + "\n")
    tail = runlog.read_tail(path, 5)
    assert tail == ["line75", "line76", "line77", "line78", "line79"]
    assert runlog.newest(tmp_path) == path


def test_follow_streams_new_lines_until_idle(tmp_path):
    path = tmp_path / "runs" / "x.log"
    path.parent.mkdir(parents=True)
    path.write_text("first\n")
    seen: list[str] = []

    def grow() -> None:
        time.sleep(0.1)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("second\nthird\n")

    worker = threading.Thread(target=grow)
    worker.start()
    runlog.follow(path, stop_after_idle=1.5, poll=0.1,
                  printer=seen.append)
    worker.join()
    assert seen == ["first", "second", "third"]


def test_list_and_tail_parse(tmp_path, monkeypatch, capsys):
    from ogtool.cli import run

    monkeypatch.chdir(tmp_path)
    example = Path(__file__).resolve().parents[1] / "config.example.yaml"
    (tmp_path / "c.yaml").write_text(example.read_text())
    (tmp_path / "data" / "logs" / "runs").mkdir(parents=True)
    (tmp_path / "data" / "logs" / "runs" / "demo-1.log") \
        .parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "logs" / "runs" / "demo-1.log").write_text("hello\n")
    assert run(["--config", str(tmp_path / "c.yaml"),
                "logs"]) == 0
    assert "demo-1.log" in capsys.readouterr().out
    assert run(["--config", str(tmp_path / "c.yaml"),
                "logs", "tail", "-n", "1",
                "--file", str(tmp_path / "data" / "logs" / "runs" / "demo-1.log"),
                ]) == 0
    assert "hello" in capsys.readouterr().out
