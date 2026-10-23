"""#14 video manage - join or leave one group with a set of accounts.

Fields: the group link, the operation interval in milliseconds, the progress
counter, and the joined account list.  The page has join / leave / stop / refresh;
``--action`` picks one and the run reports progress per account.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import ConfigError, Settings
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

FEATURE = "video_manage"
SECTION = "video_manage"
TITLE = "#14 视频管理"

JOIN = "join"
LEAVE = "leave"
STOP = "stop"

FLAGS = (
    Flag("--link", "link", str, "the group link"),
    Flag("--interval-ms", "interval_ms", int, "operation interval in milliseconds"),
    Flag("--action", "action", str, "join|leave|stop"),
)


@dataclass(slots=True)
class VideoConfig:
    account_labels: list[str] = field(default_factory=list)
    link: str = ""
    interval_ms: int = 1000
    action: str = JOIN
    task_name: str = "video-manage"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> VideoConfig:
        del settings
        data = section(raw, SECTION)
        link = text(data, "link")
        if not link:
            raise ConfigError(f"{SECTION}: give the group link (--link)")
        action = str(data.get("action") or JOIN).lower()
        if action not in (JOIN, LEAVE, STOP):
            raise ConfigError(f"{SECTION}: action must be join, leave or stop")
        return cls(
            account_labels=accounts_of(raw, data),
            link=link,
            interval_ms=num(data, "interval_ms", 1000, kind=int, minimum=0),
            action=action,
            task_name=name_of(raw, data, "video-manage"),
        )


def run(config: VideoConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts] or ["-"]
    target = parse_many([config.link], "group")[0]

    header(
        config.task_name,
        link=target.ref,
        interval=f"{config.interval_ms} ms",
        action=config.action,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
    )

    if config.action == STOP:
        print("进度：0/0  已加入账号：-")
        print("PLAN: 0 operations, stop requested before the first account")
        return 0

    task = open_task(ctx, config.task_name, FEATURE,
                     {"link": target.ref, "action": config.action}, len(labels))
    sender = ctx.sender()

    joined: list[str] = []
    failed = 0
    for index, account in enumerate(labels, start=1):
        result = (sender.join(account, target) if config.action == JOIN
                  else sender.leave(account, target))
        if result.ok:
            joined.append(account)
            record(ctx, task, account, target.ref, True)
        else:
            failed += 1
            record(ctx, task, account, target.ref, False, result.code)
        print(f"进度：{index}/{len(labels)}  {account} -> "
              f"{'ok' if result.ok else result.code}")

    print(f"已加入账号：{', '.join(joined) or '-'}")
    print(f"PLAN: {len(labels)} operations, {len(joined)} ok, {failed} failed")
    close_task(ctx, task, ok=len(joined), failed=failed)
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(VideoConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["JOIN", "LEAVE", "STOP", "TITLE", "VideoConfig", "main", "run"]
