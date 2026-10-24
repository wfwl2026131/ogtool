"""#13 filter groups - sort groups into the page's six buckets.

Fields: the group list, concurrent accounts, the global request interval in
milliseconds, the per account interval in milliseconds, the deep check toggle,
and the six counters (can send / needs approval / muted / channel / invalid / user) plus the
output directory.

``deep check`` is what the page warns about: the account has to join every group to
see whether it needs approval and to read its real numbers, so the run is slower
and costs more accounts.  Here it decides whether the answer may be trusted for
the needs approval bucket; without it the same answer is reported as can send.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import ConfigError, Settings
from ..flags import Flag
from ..limiter import format_duration
from ..progress import Stats
from ..report import write_lines
from ..runtime import RunContext
from ..simulate import CHAT_WRITE_FORBIDDEN
from ..targets import collect, parse_many
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    name_of,
    num,
    open_task,
    out_dir,
    pause,
    record,
    section,
    text,
)

FEATURE = "filter_group"
SECTION = "filter_group"
TITLE = "#13 筛选群组"

CAN_SEND = "可发言"
NEEDS_APPROVAL = "需验证"
MUTED = "全局禁言"
CHANNEL = "频道"
INVALID = "无效"
USER = "用户"

BUCKETS = (CAN_SEND, NEEDS_APPROVAL, MUTED, CHANNEL, INVALID, USER)

FLAGS = (
    Flag("--groups", "groups", str, "groups, comma separated"),
    Flag("--groups-file", "files", str, "file with one group per line",
         action="append"),
    Flag("--threads", "threads", int,
         "one account probes one group at a time (planning only)"),
    Flag("--global-interval-ms", "global_interval_ms", int,
         "global request interval in milliseconds"),
    Flag("--account-interval-ms", "account_interval_ms", int,
         "per account request interval in milliseconds"),
    Flag("--deep", "deep", None, "deep check (join to see the details)",
         action="store_true"),
    Flag("--output-dir", "output_dir", str, "where the six lists go"),
)


def classify(code: str, ok: bool, deep: bool) -> str:
    """Which of the six buckets this answer belongs to."""
    if code == "USER":
        return USER
    if code == "CHANNEL":
        return CHANNEL
    if code == CHAT_WRITE_FORBIDDEN:
        return MUTED
    if code == "JOIN_REQUEST":
        return NEEDS_APPROVAL if deep else CAN_SEND
    if ok or code == "CAN_SEND":
        return CAN_SEND
    return INVALID


@dataclass(slots=True)
class GroupConfig:
    account_labels: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    threads: int = 1
    global_interval_ms: int = 500
    account_interval_ms: int = 800
    deep: bool = False
    output_dir: str = ""
    task_name: str = "filter-group"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> GroupConfig:
        del settings
        data = section(raw, SECTION)
        inline = data.get("groups") or data.get("inline") or ""
        if isinstance(inline, (list, tuple)):
            inline = ",".join(str(item) for item in inline)
        files = data.get("files") or []
        if isinstance(files, str):
            files = [files]
        groups = [target.ref for target in collect(
            inline=str(inline) if inline else None,
            files=list(files), kind="group")]
        if not groups:
            raise ConfigError(f"{SECTION}: no group given (--groups)")
        return cls(
            account_labels=accounts_of(raw, data),
            groups=groups,
            threads=num(data, "threads", 1, kind=int, minimum=1),
            global_interval_ms=num(data, "global_interval_ms", 500, kind=int),
            account_interval_ms=num(data, "account_interval_ms", 800, kind=int),
            deep=bool(data.get("deep", False)),
            output_dir=text(data, "output_dir"),
            task_name=name_of(raw, data, "filter-group"),
        )


def gap(config: GroupConfig, previous: str = "", account: str = "") -> float:
    """Seconds before one resolve.

    ``global_interval_ms`` paces every request; a group handled by the account
    that handled the previous one also has to clear ``account_interval_ms``.
    """
    wait = config.global_interval_ms / 1000.0
    if previous and previous == account:
        wait = max(wait, config.account_interval_ms / 1000.0)
    return wait


def run(config: GroupConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts] or ["-"]

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        groups=len(config.groups),
        threads=config.threads,
        global_interval=f"{config.global_interval_ms} ms",
        account_interval=f"{config.account_interval_ms} ms",
        deep="on" if config.deep else "off",
        output_dir=config.output_dir or "-",
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"groups": config.groups, "deep": config.deep},
                     len(config.groups))
    sender = ctx.sender()
    stats = Stats(total=len(config.groups))
    buckets: dict[str, list[str]] = {name: [] for name in BUCKETS}
    current = "-"
    previous_account = ""
    waited = 0.0

    for index, group in enumerate(config.groups, start=1):
        current = group
        account = labels[(index - 1) % len(labels)]
        if index > 1:
            delay = gap(config, previous_account, account)
            waited += delay
            pause(ctx, delay)
        previous_account = account
        target = parse_many([group], "group")[0]
        result = sender.resolve(account, target)
        bucket = classify(result.code, result.ok, config.deep)
        buckets[bucket].append(group)
        stats.record(result.ok, result.code)
        record(ctx, task, account, group, result.ok, result.code, bucket)
        print(f"[{index:03d}/{stats.total:03d}] {group} -> {bucket}"
              f"{'' if result.ok else ' (' + result.code + ')'}")

    print(f"当前：{current}")
    for name in BUCKETS:
        print(f"{name}：{len(buckets[name])}")
    for line in stats.lines("Processed"):
        print(line)

    base = config.output_dir or out_dir(ctx, {}, ctx.settings)
    for name in BUCKETS:
        write_lines(f"{base}/filter_group_{_slug(name)}.txt", buckets[name],
                    header=name)
    print(f"输出目录：{base}")
    if ctx.live and waited:
        print(f"waited: {format_duration(waited)}")
    print(f"PLAN: {len(config.groups)} groups, {len(accounts)} accounts, "
          f"can_send {len(buckets[CAN_SEND])}")
    close_task(ctx, task, ok=stats.ok, failed=stats.failed)
    return 0


def _slug(name: str) -> str:
    table = {CAN_SEND: "can_send", NEEDS_APPROVAL: "needs_approval",
             MUTED: "muted", CHANNEL: "channel", INVALID: "invalid",
             USER: "user"}
    return table.get(name, "other")


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(GroupConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["BUCKETS", "CAN_SEND", "CHANNEL", "GroupConfig", "INVALID",
           "MUTED", "NEEDS_APPROVAL", "TITLE", "USER", "classify", "main",
           "run"]
