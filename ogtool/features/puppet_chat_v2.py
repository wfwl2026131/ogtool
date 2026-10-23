"""#19 话术炒群 v2 — the owner accounts play the script into their own chats.

Same player as #0 with three differences the page makes visible: the targets
must be groups or channels the accounts own, the accounts are started one after
another (``不同号启动间隔``) instead of together, and there is no polish switch
because nothing is rewritten on this page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import chat_pattern
from ..config import ConfigError, Settings
from ..flags import Flag
from ..limiter import format_duration
from ..runtime import RunContext
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    name_of,
    num,
    open_task,
    section,
    targets_of,
    text,
)
from .puppet_chat import PuppetConfig, play, resolve_pattern

FEATURE = "puppet_chat_v2"
SECTION = "puppet_chat_v2"
TITLE = "#19 话术炒群v2"

FLAGS = (
    Flag("--targets", "targets", str, "own groups/channels, comma separated"),
    Flag("--targets-file", "files", str, "file with one target per line",
         action="append"),
    Flag("--pattern", "pattern", str, "script file (话术文件)"),
    Flag("--mode-file", "mode_file", str, "mode file, optional"),
    Flag("--mode", "mode", str, "mode name inside the mode file"),
    Flag("--startup-interval", "startup_interval", float,
         "seconds between two accounts starting"),
    Flag("--loop-interval", "loop_interval", float,
         "minutes between two loops"),
    Flag("--loops", "loops", int, "how many times the script plays"),
    Flag("--multiplier", "multiplier", int, "message multiplier"),
    Flag("--daily-limit", "daily_limit", int,
         "text messages one account may send per day"),
)


@dataclass(slots=True)
class PuppetV2Config:
    account_labels: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    pattern: str = ""
    mode_file: str = ""
    mode: str = ""
    startup_interval: float = 10.0
    loop_interval: float = 5.0
    loops: int = 1
    multiplier: int = 1
    daily_limit: int = 0
    pictures: str = ""
    task_name: str = "puppet-chat-v2"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> PuppetV2Config:
        data = section(raw, SECTION)
        targets = [target.ref for target in targets_of(data, kind="group")]
        if not targets:
            raise ConfigError(f"{SECTION}: no target given (--targets)")
        if not text(data, "pattern") and not text(data, "mode_file"):
            raise ConfigError(f"{SECTION}: no script or mode file given")
        pictures = text(data, "pictures") or str(
            chat_pattern.default_picture_dir(settings.data_dir)
        )
        return cls(
            account_labels=accounts_of(raw, data),
            targets=targets,
            pattern=text(data, "pattern"),
            mode_file=text(data, "mode_file"),
            mode=text(data, "mode"),
            startup_interval=num(data, "startup_interval", 10.0),
            loop_interval=num(data, "loop_interval", 5.0),
            loops=num(data, "loops", 1, kind=int, minimum=1),
            multiplier=num(data, "multiplier", 1, kind=int, minimum=1),
            daily_limit=num(data, "daily_limit", 0, kind=int, minimum=0),
            pictures=pictures,
            task_name=name_of(raw, data, "puppet-chat-v2"),
        )


def startup_offsets(labels: list[str], interval: float) -> dict[str, float]:
    """Account ``i`` starts ``i * interval`` seconds after the first one."""
    return {label: index * max(0.0, interval)
            for index, label in enumerate(labels)}


def run(config: PuppetV2Config, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts]
    base = Path(ctx.settings.data_dir)
    path = resolve_pattern(config.pattern, config.mode_file, config.mode, base)
    messages = chat_pattern.load(path)
    offsets = startup_offsets(labels, config.startup_interval)

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        targets=len(config.targets),
        script=path.name,
        lines=len(messages),
        startup_interval=f"{config.startup_interval:.0f}s",
        loop_interval=f"{config.loop_interval:.0f} min",
        loops=config.loops,
        multiplier=config.multiplier,
        daily_limit=config.daily_limit or "-",
    )

    total = len(config.targets) * len(messages) * config.loops * config.multiplier
    task = open_task(ctx, config.task_name, FEATURE,
                     {"script": str(path), "targets": config.targets}, total)
    playback = PuppetConfig(
        account_labels=config.account_labels,
        targets=config.targets,
        pattern=config.pattern,
        mode_file=config.mode_file,
        mode=config.mode,
        loop_interval=config.loop_interval,
        loops=config.loops,
        multiplier=config.multiplier,
        daily_limit=config.daily_limit,
        polish=0,
        ai_polish=False,
        pictures=config.pictures,
        task_name=config.task_name,
    )
    outcome = play(ctx, task, messages, playback, labels,
                   startup_offsets=offsets)

    print(f"Processed: {outcome.processed} / {total}")
    print(f"ok: {outcome.ok}")
    print(f"failed: {outcome.failed}")
    print(f"skipped: {outcome.skipped}")
    print(f"started accounts: {', '.join(labels) or '-'}")
    print(f"PLAN: {total} messages, {max(1, len(labels))} accounts, "
          f"ETA {format_duration(outcome.waited)}")
    close_task(ctx, task, ok=outcome.ok, failed=outcome.failed)
    return 0 if not outcome.failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(PuppetV2Config.from_dict(raw, ctx.settings), ctx)


__all__ = ["PuppetV2Config", "TITLE", "main", "run", "startup_offsets"]
