"""``ogtool selftest``: the checks this project kept doing by hand.

Three of them, each born from a specific way of fooling ourselves:

  * ``receipts`` - every run must leave a valid receipt (before this, "25 items
    ran" was a claim about stdout);
  * ``knobs``    - no config field may exist only to be printed in the header,
    and no decision about such a field may outlive it;
  * ``sweep``    - all 25 pages must still dry-run, each with a real fixture.

They used to live in a shell habit. A habit is not shipped with the tool, so
this module is the habit with an exit code.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

CHECKS = ("receipts", "knobs", "sweep", "docs")

#: Where the two project notes live. They are outside this checkout, so a
#: missing directory is reported as "skipped", never as a failure.
DOC_PATHS: tuple[Path, ...] = (
    Path.home() / ".dsh/bot/workspace/proj-tdesktop/coder/参考端-隐藏机制盘点.md",
    Path.home() / ".dsh/bot/workspace/proj-tdesktop/coder/功能对齐-25项-对照表.md",
)

#: The number a note declares as current: ``<!-- current: pytest → 890 passed -->``
CURRENT = re.compile(r"<!--\s*current:\s*pytest\s*(?:→|->)\s*(\d+)\s*passed\s*-->")


def root() -> Path:
    """The checkout this module is running from."""
    return Path(__file__).resolve().parent.parent


def check_receipts(settings: Any) -> tuple[str, list[str]]:
    """Every receipt on disk must validate."""
    from . import receipts

    directory = receipts.receipt_dir(settings)
    bodies, problems = receipts.read_all(directory)
    for body in bodies:
        problems.extend(f"{body.get('feature', '?')}: {problem}"
                        for problem in receipts.validate(body))
    return f"receipts: {len(bodies)} receipt(s), {len(problems)} problem(s)", \
        problems


def check_knobs(base: Path | None = None) -> tuple[str, list[str]]:
    """The knob audit, called as a module rather than through a shell."""
    target = (base or root()) / "scripts" / "knob_audit.py"
    if not target.exists():
        return "knobs: audit script missing", [f"{target} not found"]
    spec = importlib.util.spec_from_file_location("og_knob_audit", target)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        return "knobs: audit script unreadable", [str(target)]
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = module.main([])
    summary = buffer.getvalue().strip().splitlines()[-1] if buffer.getvalue() \
        else "knobs: no output"
    problems = [] if code == 0 else [summary]
    return f"knobs: {summary}", problems


def check_sweep(base: Path | None = None,
                timeout: float = 900.0) -> tuple[str, list[str]]:
    """All 25 dry runs, in a subprocess: one page cannot poison another."""
    here = base or root()
    script = here / "scripts" / "sweep.py"
    if not script.exists():
        return "sweep: script missing", [f"{script} not found"]
    run = subprocess.run(  # noqa: S603 - our own script, fixed arguments
        [sys.executable, str(script)], cwd=here, capture_output=True,
        text=True, timeout=timeout, check=False)
    output = (run.stdout or "").strip().splitlines()
    summary = output[-1] if output else "sweep: no output"
    problems = [] if run.returncode == 0 else output[-12:]
    return f"sweep: {summary}", problems


def collected_tests(base: Path | None = None,
                    timeout: float = 300.0) -> tuple[int, list[str]]:
    """How many tests pytest can collect right now."""
    here = base or root()
    # Without -q: the quiet form prints one line per file, and only the full
    # form ends with "890 tests collected".
    run_pytest = subprocess.run(  # noqa: S603 - our own tree, fixed arguments
        [sys.executable, "-m", "pytest", "--collect-only"],
        cwd=here, capture_output=True, text=True, timeout=timeout,
        check=False, env={**_env(), "PYTHONPATH": str(here)})
    output = [line for line in (run_pytest.stdout or "").splitlines() if line]
    for line in reversed(output):
        match = re.match(r"^(\d+) tests? collected", line.strip())
        if match:
            return int(match.group(1)), []
    return 0, [f"pytest could not collect: {(run_pytest.stderr or '')[-200:]}"]


def _env() -> dict[str, str]:
    import os

    return dict(os.environ)


def check_docs(paths: tuple[Path, ...] | None = None,
               expected: int | None = None) -> tuple[str, list[str]]:
    """The count a note claims must be the count pytest can still collect.

    These notes have carried a stale 'passed: N' more than once, and a number
    nobody re-derives is the easiest lie in a document. A note that is not on
    this machine is skipped rather than failed - the code travels, the notes may
    not.
    """
    targets = paths or DOC_PATHS
    problems: list[str] = []
    if expected is None:
        expected, failures = collected_tests()
        problems.extend(failures)
    seen = 0
    for path in targets:
        if not path.exists():
            print(f"docs: {path} not on this machine, skipped")
            continue
        seen += 1
        match = CURRENT.search(path.read_text(encoding="utf-8"))
        if match is None:
            problems.append(f"{path.name}: no '<!-- current: pytest -> N -->' "
                            "marker")
            continue
        claimed = int(match.group(1))
        if claimed != expected:
            problems.append(f"{path.name}: claims {claimed} passed, "
                            f"pytest collects {expected}")
    return (f"docs: {seen} note(s) checked against {expected} collected "
            f"test(s)"), problems


def run(settings: Any, only: list[str] | None = None,
        timeout: float = 900.0) -> int:
    """Run the checks and print one line each; nonzero when anything failed."""
    wanted = [name for name in CHECKS if not only or name in only]
    problems: list[str] = []
    for name in wanted:
        if name == "receipts":
            line, found = check_receipts(settings)
        elif name == "knobs":
            line, found = check_knobs()
        elif name == "docs":
            line, found = check_docs()
        else:
            line, found = check_sweep(timeout=timeout)
        print(line)
        problems.extend(found)
    print(f"selftest: {len(wanted)} check(s), {len(problems)} problem(s)")
    for problem in problems:
        print(f"  {problem}")
    return 0 if not problems else 1


__all__ = ["CHECKS", "CURRENT", "DOC_PATHS", "check_docs", "check_knobs",
           "check_receipts", "check_sweep", "collected_tests", "root", "run"]
