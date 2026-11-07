"""#T6 Profile Collect - the group's members as a dataset.

The page collects what a group's members show: nicknames, bios and
avatars (``Deep::ProfileCollectController / Worker / Progress``,
:2184877-2185031).  Its own flow (:1742619-1742655): a group link, an
access check, the join, the collection, and the three counters it
reports live (``Nickname: %1/%2 / Bio: %1/%2 / Avatar: %1/%2``,
``Processed %1/%2``); the ending is one of the page's own four verdicts
(``Collection completed successfully`` / ``Collection stopped`` /
``Collection finished, insufficient data`` / ``Collection paused``).

The dataset lands as three artefacts: ``nicknames.txt``, ``bios.txt``
and the ``avatars/`` folder (:1742646 ``_%1_%2``, :1742647 the avatar
directory refusal).  A dry run plans the round without a session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..flags import Flag
from ..runtime import RunContext
from ..targets import parse_many
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    name_of,
    num,
    open_task,
    record,
    section,
    text,
)

FEATURE = "profile_collect"
SECTION = "profile_collect"
TITLE = "#T6 资料采集"

FLAGS = (
    Flag("--group", "group", str, "Group Link: the chat to collect from"),
    Flag("--limit", "limit", int, "how many members to read (0 = all)"),
    Flag("--out-dir", "out_dir", str, "Output Directory: where the data lands"),
    Flag(
        "--avatars/--no-avatars",
        "avatars",
        None,
        "download the avatars folder as well",
        action="bool_pair",
    ),
    Flag(
        "--min-fraction",
        "min_fraction",
        float,
        "share of members that must carry a name for a full verdict",
    ),
)

#: The page's ending verdicts (:1742651-1742654).
VERDICT_OK = "Collection completed successfully"
VERDICT_STOPPED = "Collection stopped"
VERDICT_INSUFFICIENT = "Collection finished, insufficient data"
VERDICT_PAUSED = "Collection paused"


@dataclass(slots=True)
class ProfileConfig:
    account_labels: list[str] = field(default_factory=list)
    group: str = ""
    limit: int = 0
    out_dir: str = ""
    avatars: bool = True
    min_fraction: float = 0.5
    task_name: str = "profile-collect"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> ProfileConfig:
        data = section(raw, SECTION)
        return cls(
            account_labels=accounts_of(raw, data),
            group=text(data, "group"),
            limit=int(num(data, "limit", 0, kind=int, minimum=0)),
            out_dir=text(data, "out_dir"),
            avatars=bool(data.get("avatars", True)),
            min_fraction=num(data, "min_fraction", 0.5),
            task_name=name_of(raw, data, "profile-collect"),
        )


def parse_row(item: str) -> tuple[str, str, str]:
    """``display|bio|avatar`` -> the three fields, empties allowed."""
    display, _, rest = item.partition("|")
    bio, _, avatar = rest.partition("|")
    return display.strip(), bio.strip(), avatar.strip()


def write_datasets(out_dir: Path, rows: list[tuple[str, str, str]]) -> dict[str, int]:
    """The page's three artefacts, with its own counters as the return."""
    out_dir.mkdir(parents=True, exist_ok=True)
    names = out_dir / "nicknames.txt"
    bios = out_dir / "bios.txt"
    names.write_text(
        "\n".join(row[0] for row in rows if row[0]) + "\n", encoding="utf-8"
    )
    bios.write_text(
        "\n".join(row[1] for row in rows if row[1]) + "\n", encoding="utf-8"
    )
    avatars = out_dir / "avatars"
    if any(row[2] for row in rows):
        avatars.mkdir(exist_ok=True)
    return {
        "nicknames": sum(1 for row in rows if row[0]),
        "bios": sum(1 for row in rows if row[1]),
        "avatars": sum(1 for row in rows if row[2]),
    }


def run(config: ProfileConfig, ctx: RunContext) -> int:
    if not config.group.strip():
        # The page's two link refusals (:1742637-1742638).
        print("No group link provided")
        return 2
    out_dir = (
        Path(config.out_dir).expanduser()
        if config.out_dir
        else ctx.settings.data_dir / "profiles"
    )
    accounts = ctx.bind(config.account_labels)
    handlers = [account.label for account in accounts]
    header(
        TITLE,
        accounts=f"{len(accounts)}"
        + (" (no account bound, plan only)" if not accounts else ""),
        group=config.group,
        limit=config.limit or "-",
        out=str(out_dir),
        avatars="yes" if config.avatars else "no",
    )
    task = open_task(
        ctx,
        config.task_name,
        FEATURE,
        {"group": config.group, "out": out_dir.name},
        planned=config.limit or 0,
    )
    target = parse_many([config.group], "group")[0]
    if not ctx.live or not handlers:
        print(f"PLAN {target.display()} -> collect into {out_dir}")
        close_task(ctx, task, ok=0, failed=0, status="dry-run")
        return 0
    print("Checking group access")
    sender = ctx.sender()
    resolve = sender.resolve(handlers[0], target)
    if not resolve.ok:
        print("Unable to access the group")
        close_task(ctx, task, ok=0, failed=1)
        return 1
    print("Group accessible, starting collection")
    result = sender.profiles(
        handlers[0],
        target,
        limit=config.limit,
        avatars=str(out_dir / "avatars") if config.avatars else "",
    )
    if not result.ok:
        print("Collection stopped")
        close_task(ctx, task, ok=0, failed=1)
        return 1
    rows = [parse_row(item) for item in (result.items or [])]
    counters = write_datasets(out_dir, rows)
    total = max(1, len(rows))
    print(
        f"Nickname: {counters['nicknames']}/{total}  "
        f"Bio: {counters['bios']}/{total}  "
        f"Avatar: {counters['avatars']}/{total}"
    )
    print(f"Processed {len(rows)}/{len(rows)}")
    named_share = counters["nicknames"] / total
    if named_share < config.min_fraction:
        print(VERDICT_INSUFFICIENT)
    else:
        print(VERDICT_OK)
    print(f"Saved to: {out_dir}")
    record(
        ctx, task, handlers[0], target.display(), True, detail=f"{len(rows)} member(s)"
    )
    close_task(ctx, task, ok=len(rows), failed=0)
    return 0


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(ProfileConfig.from_dict(raw, ctx.settings), ctx)


__all__ = [
    "FEATURE",
    "ProfileConfig",
    "VERDICT_INSUFFICIENT",
    "VERDICT_OK",
    "VERDICT_PAUSED",
    "VERDICT_STOPPED",
    "parse_row",
    "run",
    "write_datasets",
]
