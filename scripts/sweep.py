"""Run every feature twice in dry-run mode and report what broke.

The old copy of this lived in /tmp and vanished with it; a sweep that cannot be
re-run is not evidence. Each feature is invoked in its own process (the CLI's own
path, not a direct call), so a crash in one cannot hide another, and the report is
a single line:
``runs: N  receipts: M  problems: K  twice: A/N identical``.

The second run is not padding. Two runs of the same page with the same fixture
must leave the same receipt; when they do not, the page is making a decision the
configuration does not explain (a hidden random draw, a leftover from the first
run, a counter that only grows). That is worth knowing before trusting any
"it worked" claim, and it is only visible by doing it twice.
"""

from __future__ import annotations

import json
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
    "ai_clone": ["--targets", "@sweep", "--sources", "@source",
                 "--message", "@source|Sender|hello post"],
    # ai_comment draws which posts get a comment, a like or a sticker, so it is
    # randomised by design; its own --seed is what makes two runs comparable, and
    # a seed is part of the configuration, not a way around the check.
    "ai_comment": ["--targets", "@sweep", "--seed", "1"],
    "batch_invite": ["--groups", "@sweep", "--members", "@sweepmember"],
    "batch_invite_v2": ["--targets", "@sweep", "--contacts", "+10000000001"],
    # A rule plus one simulated incoming message: without both, the page has
    # nothing to match and plans zero replies.
    "auto_reply": ["--rule", "hello=>hi", "--simulate",
                   "@peer|Sender|hello there"],
    "batch_join": ["--targets", "@sweep"],
    "batch_login": ["--import-folder", "{tmp}/sessions"],
    "batch_rename": ["--file", "{tmp}/names.txt"],
    # copy_chat has nothing to copy in a dry run unless a message was captured
    # beforehand, and the capture needs a peer that is actually in --sources.
    "copy_chat": ["--targets", "@sweep", "--sources", "@sweepsource",
                  "--message", "@sweepsource|Sender|hello"],
    "filter_group": ["--groups", "@sweep"],
    "filter_phone": ["--numbers", "+10000000001"],
    "mass_send": ["--targets", "@sweep", "--text", "sweep"],
    # Its own distribution: own contacts are a live fetch, so the fixture gives
    # it what the page distributes instead of leaving the plan empty.
    "mass_send_v2": ["--text", "sweep", "--targets", "@sweep"],
    "mix_market": ["--targets", "@sweep"],
    "puppet_chat": ["--targets", "@sweep", "--pattern", "{tmp}/lines.txt"],
    "puppet_chat_v2": ["--targets", "@sweep", "--pattern", "{tmp}/lines.txt"],
    "video_manage": ["--link", "https://t.me/sweep"],
    "watch_chat": ["--targets", "@sweep", "--relay", "@relaychat",
                   "--simulate", "@sweep|Sender|hello world"],
    # The workbench translates one text through the template fallback and
    # reports zero usage: a sweep run must stay exactly reproducible.
    "translate": ["--text", "sweep line", "--lg-to", "zh"],
    # The transfer plans one group with a 2FA password present, so the plan
    # is deterministic (the missing-password skip would also be, but the
    # happy path proves more of the page).
    "group_manage": ["--groups", "https://t.me/sweep", "--target", "t1",
                     "--password", "sweep"],
}


def features() -> list[str]:
    sys.path.insert(0, str(ROOT))
    from ogtool.features import load_defaults, names

    load_defaults()
    return names()


def scratch_config(prefix: str = "ogsweep-cfg-") -> tuple[Path, Path]:
    """A config whose logs, db and sessions live in its own directory.

    Without it every run would write its receipt into the project's ``logs/``,
    where the previous run's receipts are still sitting - and "a receipt exists"
    would be true before the feature even started. Returns the config path and the
    directory its receipts land in.
    """
    scratch = Path(tempfile.mkdtemp(prefix=prefix))
    path = scratch / "config.yaml"
    path.write_text(
        "api_id: 1\napi_hash: sweep\n"
        f"data_dir: {scratch / 'data'}\n"
        f"session_dir: {scratch / 'sessions'}\n"
        f"db: {scratch / 'og.sqlite'}\n"
        f"logs: {scratch / 'logs'}\n",
        encoding="utf-8")
    _seed_account(scratch, path)
    return path, scratch / "logs" / "receipts"


def _seed_account(scratch: Path, config: Path) -> None:
    """Give the scratch run one account, so pages actually plan something.

    Without it every page planned zero rows, and a receipt whose counts and rows
    agree by both being empty proves very little: mass_send closed a dry run with
    ok=0 while its rows said 1, and the sweep could not see it because there was
    no row to disagree with. The session file is a one-byte stand-in; a dry run
    never opens it.
    """
    sys.path.insert(0, str(ROOT))
    from ogtool.accounts import Account
    from ogtool.config import Settings
    from ogtool.store import Store

    sessions = scratch / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    session = sessions / "sweep.session"
    session.write_bytes(b"x")
    settings = Settings.load(config)
    store = Store(settings.db_path)
    try:
        store.add_account(Account(label="sweep", phone="+10000000000",
                                  session=str(session), proxy="",
                                  status="imported"))
    finally:
        store.close()


def receipts_here(directory: Path) -> tuple[list[dict], list[str]]:
    """The receipts in one run's directory, plus anything already wrong with them."""
    sys.path.insert(0, str(ROOT))
    from ogtool import receipts as receipts_module

    bodies, problems = receipts_module.read_all(directory)
    for body in bodies:
        for problem in receipts_module.validate(body):
            problems.append(f"{body.get('feature', '?')}: {problem}")
    return bodies, problems


def fixtures() -> dict[str, list[str]]:
    """The fixture table with ``{tmp}`` filled in by real files."""
    scratch = Path(tempfile.mkdtemp(prefix="ogsweep-"))
    (scratch / "names.txt").write_text("Sweep One\nSweep Two\n")
    (scratch / "lines.txt").write_text("A0|L0|D0|hello\n")
    # batch_login imports session files; the dry run reports them instead of
    # copying them, so a one-byte stand-in is all the fixture needs.
    sessions = scratch / "sessions"
    sessions.mkdir()
    (sessions / "one.session").write_bytes(b"x")
    return {name: [item.format(tmp=scratch) for item in args]
            for name, args in FIXTURES.items()}


def dry_run(name: str, table: dict[str, list[str]]) -> tuple[int, str, list[dict]]:
    """One feature, one throwaway config, and whatever receipt it left."""
    config, receipts_dir = scratch_config()
    run = subprocess.run(  # noqa: S603 - our own CLI, fixed arguments
        [sys.executable, "-m", "ogtool", "--config", str(config),
         name, "--dry-run", *table.get(name, [])],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    bodies, _problems = receipts_here(receipts_dir)
    return run.returncode, run.stdout + run.stderr, bodies


def record_path() -> Path:
    """Where the sweep leaves its summary for ``ogtool evidence`` to read."""
    return ROOT / "logs" / "sweep.json"


def record(bodies: list[dict]) -> None:
    """Keep the receipts of the first run per feature.

    The sweep's own runs live in throwaway directories on purpose; without this
    file the project has no way to say "these 25 pages ran" - and evidence would
    have to call every feature untested.
    """
    path = record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    import datetime

    path.write_text(json.dumps({
        "when": datetime.datetime.now(datetime.UTC)
        .replace(microsecond=0).isoformat(),
        "features": {body["feature"]: body for body in bodies},
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv: list[str]) -> int:
    wanted = argv[1:] or features()
    table = fixtures()
    sys.path.insert(0, str(ROOT))
    from ogtool import receipts as receipts_module

    problems: list[str] = []
    identical = 0
    kept: list[dict] = []
    for name in wanted:
        first_code, first_out, first = dry_run(name, table)
        second_code, second_out, second = dry_run(name, table)
        if first_code != 0:
            problems.append(f"{name}: exit {first_code}\n{first_out[-600:]}")
        elif "Traceback" in first_out:
            problems.append(f"{name}: traceback\n{first_out[-600:]}")
        if second_code != 0:
            problems.append(f"{name}: second run exit {second_code}"
                            f"\n{second_out[-600:]}")
        if len(first) != 1 or len(second) != 1:
            problems.append(f"{name}: {len(first)} and {len(second)} receipt(s), "
                            "expected one each")
            print(f"{name:16s} FAIL")
            continue
        for body in first + second:
            problems.extend(f"{name}: {problem}"
                            for problem in receipts_module.validate(body))
        changes = receipts_module.diff(first[0], second[0])
        if changes:
            detail = ", ".join(key for key, _old, _new in changes)
            problems.append(f"{name}: two runs of the same config differ "
                            f"({detail})")
            print(f"{name:16s} DIFF")
        else:
            identical += 1
            kept.append(first[0])
            print(f"{name:16s} {'ok' if first_code == 0 else 'FAIL'}")
    if kept:
        record(kept)
    print(f"runs: {len(wanted)}  receipts: {len(wanted)}  "
          f"problems: {len(problems)}  "
          f"twice: {identical}/{len(wanted)} identical")
    for problem in problems:
        print("-" * 60)
        print(problem)
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
