"""Run every feature once in dry-run mode and report what broke.

The old copy of this lived in /tmp and vanished with it; a sweep that cannot be
re-run is not evidence. Each feature is invoked in its own process (the CLI's own
path, not a direct call), so a crash in one cannot hide another, and the report is
a single line: ``runs: N  problems: M``.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


#: Minimal fixtures so every feature actually reaches its dry-run path. A sweep
#: that only proves "the CLI rejects a missing argument" proves nothing about the
#: feature behind it.
FIXTURES: dict[str, list[str]] = {
    "add_contact": ["--contacts", "+10000000001"],
    "ai_chat": ["--targets", "@sweep"],
    "ai_clone": ["--targets", "@sweep", "--sources", "@source"],
    "ai_comment": ["--targets", "@sweep"],
    "batch_invite": ["--groups", "@sweep", "--members", "@sweepmember"],
    "batch_invite_v2": ["--targets", "@sweep"],
    "batch_join": ["--targets", "@sweep"],
    "batch_login": ["--logout", "frozen"],
    "batch_rename": ["--file", "{tmp}/names.txt"],
    "copy_chat": ["--targets", "@sweep"],
    "filter_group": ["--groups", "@sweep"],
    "filter_phone": ["--numbers", "+10000000001"],
    "mass_send": ["--targets", "@sweep", "--text", "sweep"],
    "mass_send_v2": ["--text", "sweep"],
    "mix_market": ["--targets", "@sweep"],
    "puppet_chat": ["--targets", "@sweep", "--pattern", "{tmp}/lines.txt"],
    "puppet_chat_v2": ["--targets", "@sweep", "--pattern", "{tmp}/lines.txt"],
    "video_manage": ["--link", "https://t.me/sweep"],
    "watch_chat": ["--targets", "@sweep"],
}


def features() -> list[str]:
    sys.path.insert(0, str(ROOT))
    from ogtool.features import load_defaults, names

    load_defaults()
    return names()


LOG_DIR: list[Path] = []


def scratch_config() -> Path:
    """A config whose logs, db and sessions live in this sweep's own directory.

    Without it every run would write its receipt into the project's ``logs/``,
    where the previous run's receipts are still sitting - and "a receipt exists"
    would be true before the feature even started.
    """
    scratch = Path(tempfile.mkdtemp(prefix="ogsweep-cfg-"))
    LOG_DIR.append(scratch / "logs")
    path = scratch / "config.yaml"
    path.write_text(
        "api_id: 1\napi_hash: sweep\n"
        f"data_dir: {scratch / 'data'}\n"
        f"session_dir: {scratch / 'sessions'}\n"
        f"db: {scratch / 'og.sqlite'}\n"
        f"logs: {scratch / 'logs'}\n",
        encoding="utf-8")
    return path


def check_receipts() -> tuple[int, list[str]]:
    """Every feature this sweep ran must have left one valid receipt."""
    sys.path.insert(0, str(ROOT))
    from ogtool import receipts as receipts_module

    directory = LOG_DIR[0] / "receipts"
    bodies, problems = receipts_module.read_all(directory)
    for body in bodies:
        for problem in receipts_module.validate(body):
            problems.append(f"{body.get('feature', '?')}: {problem}")
    return len(bodies), problems


def fixtures() -> dict[str, list[str]]:
    """The fixture table with ``{tmp}`` filled in by real files."""
    scratch = Path(tempfile.mkdtemp(prefix="ogsweep-"))
    (scratch / "names.txt").write_text("Sweep One\nSweep Two\n")
    (scratch / "lines.txt").write_text("A0|L0|D0|hello\n")
    return {name: [item.format(tmp=scratch) for item in args]
            for name, args in FIXTURES.items()}


def main(argv: list[str]) -> int:
    wanted = argv[1:] or features()
    table = fixtures()
    problems: list[str] = []
    config = scratch_config()
    for name in wanted:
        run = subprocess.run(  # noqa: S603 - our own CLI, fixed arguments
            [sys.executable, "-m", "ogtool", "--config", str(config),
             name, "--dry-run", *table.get(name, [])],
            cwd=ROOT, capture_output=True, text=True, timeout=120)
        output = run.stdout + run.stderr
        if run.returncode != 0:
            problems.append(f"{name}: exit {run.returncode}\n{output[-600:]}")
        elif "Traceback" in output:
            problems.append(f"{name}: traceback\n{output[-600:]}")
        print(f"{name:16s} {'ok' if run.returncode == 0 else 'FAIL'}")
    count, receipt_problems = check_receipts()
    problems.extend(receipt_problems)
    if count != len(wanted):
        problems.append(f"receipts: {count} for {len(wanted)} run(s)")
    print(f"runs: {len(wanted)}  receipts: {count}  problems: {len(problems)}")
    for problem in problems:
        print("-" * 60)
        print(problem)
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
