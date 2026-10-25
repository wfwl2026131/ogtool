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
import tempfile
from pathlib import Path
from typing import Any

CHECKS = ("receipts", "knobs", "sweep", "docs", "evidence", "live")

#: Where the two project notes live. They are outside this checkout, so a
#: missing directory is reported as "skipped", never as a failure.
DOC_PATHS: tuple[Path, ...] = (
    Path.home() / ".dsh/bot/workspace/proj-tdesktop/coder/参考端-隐藏机制盘点.md",
    Path.home() / ".dsh/bot/workspace/proj-tdesktop/coder/功能对齐-25项-对照表.md",
)

#: Notes that must carry the generated blocks. The mechanism inventory is prose
#: and does not; the parity table does, and losing the markers there would
#: otherwise be invisible - the check simply skipped a note with no block, which
#: is how I deleted the evidence table once without a single check going red.
BLOCK_NOTES: tuple[Path, ...] = (
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


EVIDENCE_START = "<!-- evidence:start -->"
EVIDENCE_END = "<!-- evidence:end -->"
LIVE_START = "<!-- live:start -->"
LIVE_END = "<!-- live:end -->"


def evidence_block(base: Path | None = None,
                   directory: Path | None = None) -> str:
    """What the evidence block in a note should contain right now."""
    from . import evidence

    here = base or root()
    where = directory or here / "data" / "logs" / "receipts"
    rows = evidence.collect(here, where)
    return f"{EVIDENCE_START}\n{evidence.as_markdown(rows)}\n{EVIDENCE_END}"


def live_block(base: Path | None = None,
               directory: Path | None = None) -> str:
    """What the real-machine block in a note should contain right now."""
    from . import evidence

    here = base or root()
    where = directory or here / "data" / "logs" / "receipts"
    rows = evidence.collect(here, where)
    return f"{LIVE_START}\n{evidence.live_markdown(rows, where)}\n{LIVE_END}"


def check_docs(paths: tuple[Path, ...] | None = None,
               expected: int | None = None,
               block: str | None = None) -> tuple[str, list[str]]:
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
        text = path.read_text(encoding="utf-8")
        for start_mark, end_mark, expected_block, label in (
                (EVIDENCE_START, EVIDENCE_END, block, "evidence"),
                (LIVE_START, LIVE_END, live_block(), "live")):
            if path in BLOCK_NOTES and start_mark not in text:
                problems.append(f"{path.name}: the {label} markers are gone "
                                "(restore them; run scripts/sync_evidence.py)")
                continue
            start = text.find(start_mark)
            end = text.find(end_mark)
            if start < 0 or end < start:
                continue  # a note may simply not carry the table
            if expected_block is None:
                continue
            actual = text[start:end + len(end_mark)]
            if actual != expected_block:
                problems.append(f"{path.name}: the {label} block is stale "
                                "(run scripts/sync_evidence.py)")
    return (f"docs: {seen} note(s) checked against {expected} collected "
            f"test(s)"), problems


def check_evidence(settings: Any = None,
                   base: Path | None = None) -> tuple[str, list[str]]:
    """No feature may be both untested and unrun.

    This is the check that keeps the 25-item claim honest: it does not demand that
    every page has been run against Telegram, only that each one has *something*
    behind it - a test module, a sweep run, or a live receipt.
    """
    from . import evidence

    here = base or root()
    directory = (evidence.receipts_dir(settings) if settings is not None
                 else here / "data" / "logs" / "receipts")
    rows = evidence.collect(here, directory)
    problems = evidence.problems(rows)
    counts = {level: sum(1 for row in rows if row.level == level)
              for level in evidence.LEVELS}
    return (f"evidence: {len(rows)} feature(s), "
            + ", ".join(f"{level} {counts[level]}" for level in evidence.LEVELS)
            ), problems


def check_live(base: Path | None = None,
               timeout: float = 900.0) -> tuple[str, list[str]]:
    """The real-machine cases, when this machine has a login to run them with.

    Kept in the same command as the offline checks because "the install is
    healthy" includes "the session still works". Without a config.yaml there is
    nothing to check, and that is a skip rather than a failure - a checkout
    without credentials is a normal checkout.
    """
    here = base or root()
    config = here / "config.yaml"
    if not config.exists():
        return "live: no config.yaml, skipped", []
    run_smoke = subprocess.run(  # noqa: S603 - our own script, fixed arguments
        [sys.executable, str(here / "scripts/live_smoke.py")],
        cwd=here, capture_output=True, text=True, timeout=timeout,
        check=False, env={**_env(), "PYTHONPATH": str(here)})
    output = (run_smoke.stdout or "") + (run_smoke.stderr or "")
    lines = [line for line in output.splitlines() if line.strip()]
    summary = lines[-1] if lines else "no output"
    problems = gate_problems(here) if run_smoke.returncode == 0 else [
        f"live_smoke exited {run_smoke.returncode}\n{output[-800:]}"]
    return summary, problems


def gate_problems(here: Path) -> list[str]:
    """The write gate has to refuse, and the flag has to let it through.

    Two runs, each handed its own case file through ``OGTOOL_LIVE_CASES`` so the
    file naming real chats is never touched: the first must exit 2 saying it
    refused, the second must run the same case when ``--allow-write`` is given.
    Only ever checking the refusal would pass just as happily if the flag did
    nothing, which is the failure mode that hid here twice.
    """
    with tempfile.TemporaryDirectory(prefix="og-gate-") as scratch:
        refused_file = Path(scratch) / "refuse.yaml"
        refused_file.write_text(
            "cases:\n  - name: gate-canary\n    args: [runs, list]\n"
            "    write: true\n", encoding="utf-8")
        allowed_file = Path(scratch) / "allow.yaml"
        allowed_file.write_text(
            "cases:\n  - name: gate-allow\n    args: [runs, list]\n"
            "    write: true\n", encoding="utf-8")
        refused = _smoke(here, refused_file, ["--only", "gate-canary"])
        allowed = _smoke(here, allowed_file,
                         ["--allow-write", "--only", "gate-allow"])
    problems: list[str] = []
    said = (refused.stdout or "") + (refused.stderr or "")
    if refused.returncode != 2:
        problems.append(f"the write gate did not refuse (exit "
                        f"{refused.returncode})\n{said[-400:]}")
    elif "refusing" not in said or "gate-canary" not in said:
        problems.append(f"the write gate refused without naming the case\n"
                        f"{said[-400:]}")
    said = (allowed.stdout or "") + (allowed.stderr or "")
    if allowed.returncode != 0:
        problems.append(f"the flag did not let the write case through "
                        f"(exit {allowed.returncode})\n{said[-400:]}")
    elif "gate-allow" not in said or "refusing" in said:
        problems.append(f"the flag ran without running the case\n"
                        f"{said[-400:]}")
    return problems


def _smoke(here: Path, cases: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - our own script, fixed arguments
        [sys.executable, str(here / "scripts/live_smoke.py"), *args],
        cwd=here, capture_output=True, text=True, timeout=180, check=False,
        env={**_env(), "PYTHONPATH": str(here),
             "OGTOOL_LIVE_CASES": str(cases)})


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
            from . import evidence as evidence_module

            line, found = check_docs(
                block=evidence_block(root(),
                                     evidence_module.receipts_dir(settings)))
        elif name == "evidence":
            line, found = check_evidence(settings)
        elif name == "live":
            line, found = check_live()
        else:
            line, found = check_sweep(timeout=timeout)
        print(line)
        problems.extend(found)
    print(f"selftest: {len(wanted)} check(s), {len(problems)} problem(s)")
    for problem in problems:
        print(f"  {problem}")
    return 0 if not problems else 1


__all__ = ["CHECKS", "CURRENT", "DOC_PATHS", "check_docs", "check_knobs",
           "check_evidence", "check_live",
           "check_receipts", "check_sweep", "collected_tests", "root", "run"]
