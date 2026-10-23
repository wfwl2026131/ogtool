"""#10 batch rename - rename accounts from a .txt list, with pause/resume/stop.

The page owns a small state machine (``idle / running / paused / stopped``) and shows
the current account plus ``progress``, ``failed`` and ``skipped``.  A dry run replays the
same transitions so the states can be read without a session: ``--pause-at``
and ``--stop-at`` say which entry triggers them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    pause,
    read_list_file,
    record,
    section,
    text,
)

FEATURE = "batch_rename"
SECTION = "batch_rename"
TITLE = "#10 批量改名"

IDLE = "空闲"
RUNNING = "运行"
PAUSED = "已暂停"
STOPPED = "已停止"

FLAGS = (
    Flag("--file", "file", str, "the .txt with one name or 'first last' per line"),
    Flag("--operation-interval", "operation_interval", float,
         "seconds between two renames"),
    Flag("--success-cap", "success_cap", int, "stop after this many renames"),
    Flag("--flood-cap", "flood_cap", int, "PEER_FLOOD retries per entry"),
    Flag("--pause-at", "pause_at", int,
         "pause once after this entry, then continue (dry-run demo)"),
    Flag("--stop-at", "stop_at", int, "stop once before this entry (dry-run demo)"),
)


def parse_names(rows: list[str]) -> list[tuple[str, str]]:
    """``first name last name`` -> (first, last); a single word becomes the first name."""
    out: list[tuple[str, str]] = []
    for row in rows:
        parts = row.split()
        if not parts:
            continue
        if len(parts) == 1:
            out.append((parts[0], ""))
        else:
            out.append((parts[0], " ".join(parts[1:])))
    return out


@dataclass(slots=True)
class RenameState:
    """The five things the page displays, updated as the run goes."""

    total: int = 0
    processed: int = 0
    failed: int = 0
    skipped: int = 0
    current: str = "无"
    status: str = IDLE
    pause_at: int = 0
    stop_at: int = 0

    def line(self) -> str:
        return (f"进度：{self.processed}/{self.total}  失败: {self.failed}  "
                f"跳过：{self.skipped}  当前账号：{self.current}  "
                f"状态：{self.status}")


@dataclass(slots=True)
class RenameConfig:
    account_labels: list[str] = field(default_factory=list)
    names: list[tuple[str, str]] = field(default_factory=list)
    file: str = ""
    operation_interval: float = 10.0
    success_cap: int = 0
    flood_cap: int = 2
    pause_at: int = 0
    stop_at: int = 0
    task_name: str = "batch-rename"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> RenameConfig:
        del settings
        data = section(raw, SECTION)
        rows = list(data.get("entries") or [])
        file_name = text(data, "file")
        if file_name:
            rows.extend(read_list_file(file_name))
        if not rows:
            raise ConfigError(
                f"{SECTION}: give a .txt list (--file) with one name per line"
            )
        return cls(
            account_labels=accounts_of(raw, data),
            names=parse_names(rows),
            file=file_name,
            operation_interval=num(data, "operation_interval", 10.0),
            success_cap=num(data, "success_cap", 0, kind=int),
            flood_cap=num(data, "flood_cap", 2, kind=int),
            pause_at=num(data, "pause_at", 0, kind=int),
            stop_at=num(data, "stop_at", 0, kind=int),
            task_name=name_of(raw, data, "batch-rename"),
        )


def run(config: RenameConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts] or ["-"]
    state = RenameState(total=len(config.names), pause_at=config.pause_at,
                        stop_at=config.stop_at)

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        entries=len(config.names),
        operation_interval=f"{config.operation_interval:.0f}s",
        state=state.line(),
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"file": config.file, "entries": len(config.names)},
                     len(config.names))
    sender = ctx.sender()

    state.status = RUNNING
    print(f"状态：{state.status}")
    waited = 0.0
    for index, (first, last) in enumerate(config.names, start=1):
        if state.stop_at and index >= state.stop_at:
            state.status = STOPPED
            print(f"状态：{state.status}（收到停止，第 {index} 条开始前）")
            break
        if config.success_cap and state.processed >= config.success_cap:
            print(f"stopped: success_cap ({config.success_cap})")
            break

        if index > 1:
            # The rename interval is the page's anti-flood knob: one profile
            # edit per interval, on the account that does the edit.
            waited += config.operation_interval
            pause(ctx, config.operation_interval)
        account = labels[(index - 1) % len(labels)]
        state.current = account
        result = sender.update_profile(account, first, last)
        state.processed += 1
        if result.ok:
            record(ctx, task, account, f"{first} {last}".strip(), True)
        else:
            state.failed += 1
            record(ctx, task, account, f"{first} {last}".strip(), False,
                   result.code)
        print(f"[{index:03d}/{state.total:03d}] {state.line()}")

        if state.pause_at and index == state.pause_at:
            state.status = PAUSED
            print(f"状态：{state.status}")
            state.status = RUNNING
            print(f"状态：{state.status}（继续）")

    state.status = state.status if state.status == STOPPED else IDLE
    print(f"状态：{state.status}")
    if ctx.live and waited:
        print(f"waited: {format_duration(waited)}")
    print(f"PLAN: {state.processed} renames, {len(accounts)} accounts, "
          f"failed {state.failed}, skipped {state.skipped}")
    close_task(ctx, task, ok=state.processed - state.failed, failed=state.failed)
    return 0 if not state.failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(RenameConfig.from_dict(raw, ctx.settings), ctx)


__all__ = [
    "IDLE",
    "PAUSED",
    "RUNNING",
    "STOPPED",
    "RenameConfig",
    "RenameState",
    "TITLE",
    "main",
    "parse_names",
    "run",
]
