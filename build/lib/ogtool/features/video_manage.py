"""#14 video manage - drive a group call with a set of accounts.

The dump's own action list for the page is Join / Leave / Speak / Mic off, with
an "(in call)" state, a "[Muted]" marker and "success cached group call slug"
next to the config keys ``operation`` and ``intervalMs`` in
``video_manage_config.json``. So this is group-call automation, not video
upload: an account joins the call, can be switched to speaking or muted, and
the resolved group call is cached on the sender so a second action on the same
group does not resolve it again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import ConfigError, Settings
from ..flags import Flag
from ..limiter import format_duration
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
    pause,
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
SPEAK = "speak"
MIC_OFF = "mic_off"

FLAGS = (
    Flag("--link", "link", str, "the group link"),
    Flag("--interval-ms", "interval_ms", int, "operation interval in milliseconds"),
    Flag("--action", "action", str, "join|leave|speak|mic_off|stop"),
    Flag("--operation", "operation", str,
         "the page's operation key (join|leave|speak|mic_off|stop)"),
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
        action = str(data.get("operation") or data.get("action") or JOIN).lower()
        allowed = (JOIN, LEAVE, SPEAK, MIC_OFF, STOP)
        if action not in allowed:
            raise ConfigError(
                f"{SECTION}: operation must be one of "
                f"{', '.join(allowed)}, is {action}")
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

    action_label = {SPEAK: "已发言账号", MIC_OFF: "已闭麦账号"}.get(
        config.action, "")
    if config.action == STOP:
        print("进度：0/0  已加入账号：-")
        print("PLAN: 0 operations, stop requested before the first account")
        return 0

    task = open_task(ctx, config.task_name, FEATURE,
                     {"link": target.ref, "action": config.action}, len(labels))
    sender = ctx.sender()

    joined: list[str] = []
    # The page keeps one list for whichever action ran; saying "joined" after a
    # leave would be a plain lie in the run report.
    done_label = (action_label or
                  ("已加入账号" if config.action == JOIN else "已退出账号"))
    failed = 0
    waited = 0.0
    for index, account in enumerate(labels, start=1):
        if index > 1:
            delay = config.interval_ms / 1000.0
            waited += delay
            pause(ctx, delay)
        if config.action in (JOIN, LEAVE):
            result = (sender.join(account, target) if config.action == JOIN
                      else sender.leave(account, target))
        else:
            # Speak / mic-off are call actions: the sender resolves the group
            # call once and keeps it, so the next account reuses it.
            result = sender.join_call(account, target,
                                      muted=config.action == MIC_OFF)
        if result.ok:
            joined.append(account)
            record(ctx, task, account, target.ref, True)
        else:
            failed += 1
            record(ctx, task, account, target.ref, False, result.code, result.detail)
        print(f"进度：{index}/{len(labels)}  {account} -> "
              f"{'ok' if result.ok else result.code}")

    print(f"{done_label}：{', '.join(joined) or '-'}")
    if ctx.live and waited:
        print(f"waited: {format_duration(waited)}")
    print(f"PLAN: {len(labels)} operations, {len(joined)} ok, {failed} failed")
    close_task(ctx, task, ok=len(joined), failed=failed)
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(VideoConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["JOIN", "LEAVE", "MIC_OFF", "SPEAK", "STOP", "TITLE", "VideoConfig",
           "main", "run"]
