"""#24 settings: the global knobs, shown and validated, never acted on.

The reference page carries six numbers that every other page reads: how many
accounts load at once, how big the thread pool may get, the delay before a
task resumes, the overload threshold, and the two restart knobs. It shows them
and validates them; restarting and overloading are runtime behaviour of the
reference and stay out of this tool, which is why ``restart_due`` is a
computed description and not a command.

The labels are the reference's Chinese page labels, kept verbatim: this module
is the one place where the tool prints the reference's own wording next to the
flag it stands for.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any

from ..config import ConfigError
from ..flags import Flag
from ..runtime import RunContext
from . import register
from ._common import close_task, open_task, record

FEATURE = "settings"

FLAGS = (
    Flag("--json", "json", None, "print the knobs as JSON", action="store_true"),
    Flag("--global-file", "global_file", str,
         "read a deep_config.json and show the global keys (read-only)"),
)

# (key, the page label, the rule the page enforces). The rule is what "shown
# and validated" means: a knob that cannot be used is reported, not clamped,
# because a silently repaired setting is a setting nobody can trust.
KNOBS: tuple[tuple[str, str, str], ...] = (
    ("account_batch", "账号分批加载数量", "positive"),
    ("threads_max", "全局线程池最大线程数", "positive"),
    ("resume_delay", "恢复任务延迟", "non_negative"),
    ("tp_overload", "TP超载值", "non_negative"),
    ("restart_interval", "重启间隔", "non_negative"),
    ("restart_timed", "定时重启", "none"),
)


#: The global keys the binary stores in ``deep_config.json``, in the order the
#: dump lists them, with the page label for each. Keys that carry a token are
#: marked ``secret``: the value is never printed, only whether it is set.
GLOBAL_KEYS: tuple[tuple[str, str, str], ...] = (
    ("accountBatchLoadSize", "账号分批加载数量", "number"),
    ("globalThreadPoolMaxThreadCount", "全局线程池最大线程数", "number"),
    ("threadPoolOverloadThreshold", "TP超载值", "number"),
    ("restoreDelaySeconds", "恢复任务延迟（秒）", "number"),
    ("autoRestartEnabled", "自动重启", "bool"),
    ("autoRestartEnsureMonitorRunning", "重启时确保监控运行", "bool"),
    ("autoRestartIntervalMinutes", "重启间隔（分钟）", "number"),
    ("autoRestartScheduledTimesText", "定时重启时间表", "text"),
    ("deepLogMode", "日志模式", "text"),
    ("fastApiMode", "FastAPI 模式", "bool"),
    ("fastPassiveReceiveMode", "被动接收快速模式", "bool"),
    ("aiChatFastTextSendMode", "AI 聊天快速文本发送", "bool"),
    ("batchLoginCodeModeEnabled", "登录使用验证码模式", "bool"),
    ("allowUpgradeCoreVersion", "允许升级核心版本", "bool"),
    ("autoScriptSyncServiceToggle", "自动脚本同步开关", "bool"),
    ("autoScriptSyncServiceLink", "自动脚本同步地址", "text"),
    ("apiIdTdesktop", "API ID（tdesktop）", "number"),
    ("apiHashTdesktop", "API HASH（tdesktop）", "secret"),
    ("apiIdIos", "API ID（iOS）", "number"),
    ("apiHashIos", "API HASH（iOS）", "secret"),
    ("apiIdAndroid", "API ID（Android）", "number"),
    ("apiHashAndroid", "API HASH（Android）", "secret"),
    ("devicePlatformTdesktopEnabled", "设备平台：tdesktop", "bool"),
    ("devicePlatformIosEnabled", "设备平台：iOS", "bool"),
    ("devicePlatformAndroidEnabled", "设备平台：Android", "bool"),
    ("importedTdataPhoneMap", "tdata 导入手机号映射", "map"),
    ("freeMeBotToken", "FreeMe bot token", "secret"),
    ("freeMeUseLocalBot", "FreeMe 使用本地 bot", "bool"),
    ("freeMeBotApiRequestsPerSecond", "FreeMe bot 每秒请求", "number"),
    ("freeMeClearGroupEnabled", "FreeMe 清群", "bool"),
)


def read_global(path: str) -> tuple[dict[str, object], int, int]:
    """Known keys out of a ``deep_config.json``; also unknown and secret counts.

    The reference writes every page's settings into this one file, so an
    unknown key is normal rather than an error: it is counted so the report
    says how much of the file this page actually explains.
    """
    from pathlib import Path as _Path

    file = _Path(path)
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ConfigError(f"{path} is not JSON: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"{path} is not a JSON object")
    known = {key for key, _label, _kind in GLOBAL_KEYS}
    values: dict[str, object] = {}
    secrets_set = 0
    for key, _label, kind in GLOBAL_KEYS:
        if key not in data:
            continue
        if kind == "secret":
            secrets_set += 1
            values[key] = "<set>" if data[key] else "<empty>"
            continue
        values[key] = data[key]
    unknown = len([key for key in data if key not in known])
    return values, unknown, secrets_set


@dataclass(slots=True)
class SettingsPageConfig:
    """The page has no inputs of its own: it reflects the task YAML."""

    as_json: bool = False
    global_file: str = ""
    task_name: str = "settings"
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict) -> SettingsPageConfig:
        page = raw.get("settings") or {}
        return cls(as_json=bool(page.get("json")),
                   global_file=str(page.get("global_file") or ""),
                   task_name=str(page.get("task_name") or "settings"), raw=raw)


def knobs(runtime: Any) -> list[tuple[str, str, Any]]:
    """The five numeric knobs, in the order the page shows them."""
    return [(key, label, getattr(runtime, key, 0)) for key, label, _ in KNOBS]


#: The hidden gate the page documents only in its own error text: a restart
#: interval below one hour is refused unless this marker file exists.
DEBUG_MARKER = "tdata/.debug"
MIN_RESTART_SECONDS = 3600.0


def debug_gate_open(path: object = None) -> bool:
    """Whether the reference's ``tdata/.debug`` marker is present."""
    from pathlib import Path as _Path

    base = _Path(str(path)) if path else _Path(".")
    return (base / DEBUG_MARKER).exists()


def problems(runtime: Any, data_dir: object = None) -> list[str]:
    """Every knob that cannot be used as it stands, with its rule."""
    found: list[str] = []
    for key, label, rule in KNOBS:
        value = getattr(runtime, key, 0)
        if rule == "positive" and value <= 0:
            found.append(f"{key} ({label}) must be above 0, is {value}")
        elif rule == "non_negative" and value < 0:
            found.append(f"{key} ({label}) must not be negative, is {value}")
    interval = getattr(runtime, "restart_interval", 0.0)
    if 0 < interval < MIN_RESTART_SECONDS and not debug_gate_open(data_dir):
        # The page's own sentence, kept verbatim: it is the only place the
        # marker file is ever mentioned in the interface.
        found.append(
            f"restart_interval ({MIN_RESTART_SECONDS:.0f}s floor) cannot be "
            f"less than 1 hour unless {DEBUG_MARKER} exists, is {interval}")
    return found


def overload_line(runtime: Any) -> str:
    return "off" if runtime.tp_overload <= 0 else str(runtime.tp_overload)


def restart_due(runtime: Any) -> str:
    """The restart line, described and never performed."""
    if runtime.restart_interval <= 0:
        return "off"
    kind = "timed" if runtime.restart_timed else "uptime"
    return f"{runtime.restart_interval:.0f}s {kind}"


def run(config: SettingsPageConfig, ctx: RunContext) -> int:
    runtime = ctx.settings.runtime
    rows = knobs(runtime)
    data_dir = getattr(ctx.settings, "data_dir", None)
    found = problems(runtime, data_dir)
    task = _open_audit(ctx, config, rows, found)

    if config.as_json:
        # The flat object is the shape the page has always emitted, one key per
        # knob; the problems go to stderr so a pipe stays parseable.
        payload: dict[str, Any] = {key: value for key, _label, value in rows}
        if config.global_file:
            payload["global"] = read_global(config.global_file)[0]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        for line in found:
            print(f"PROBLEM: {line}", file=sys.stderr)
        return 1 if found else 0

    if config.global_file:
        values, unknown, secrets_set = read_global(config.global_file)
        print(f"global config: {config.global_file} (read-only)")
        for key, label, _kind in GLOBAL_KEYS:
            if key in values:
                print(f"  {key:<32} {label:<18} {values[key]}")
        print(f"  global keys read: {len(values)}  unknown in this page: "
              f"{unknown}  secret values shown as <set>: {secrets_set}")
    print(f"config: {ctx.settings.path}")
    print("runtime settings (no licence keys, restart is config only):")
    for key, label, value in rows:
        print(f"  {key:<18} {label:<14} {value}")
    print(f"restart due at: {restart_due(runtime)}")
    print(f"debug gate ({DEBUG_MARKER}): "
          f"{'open' if debug_gate_open(data_dir) else 'closed'}")
    print("overload check: "
          f"{'off' if runtime.tp_overload <= 0 else runtime.tp_overload}")
    for line in found:
        print(f"  PROBLEM: {line}")
    _close_audit(ctx, task, rows, found)
    return 1 if found else 0


def _open_audit(ctx: RunContext, config: SettingsPageConfig, rows: list,
                found: list[str]):
    """This page has no queue, but a run of it is still a run.

    Reading the configuration is the whole job, so the receipt records that job:
    one planned row for the audit itself, then one failed row per problem found.
    Without it the page could only ever leave the CLI's fallback receipt, which
    says "the command exited" and nothing about what it checked.
    """
    if config.as_json:
        return None  # a machine-readable dump is a pipe, not a task
    return open_task(ctx, config.task_name, FEATURE,
                     {"knobs": len(rows), "global": config.global_file},
                     planned=1)


def _close_audit(ctx: RunContext, task, rows: list, found: list[str]) -> None:
    if task is None:
        return
    record(ctx, task, "-", f"audit {len(rows)} knob(s)", True, "",
           f"{len(found)} problem(s)")
    for line in found:
        record(ctx, task, "-", line, False, "CONFIG_PROBLEM", "")
    close_task(ctx, task, derive=True)


@register(FEATURE, "settings")
def main(raw: dict, ctx: RunContext) -> int:
    return run(SettingsPageConfig.from_dict(raw), ctx)


__all__ = ["DEBUG_MARKER", "FEATURE", "FLAGS", "GLOBAL_KEYS", "KNOBS",
           "SettingsPageConfig", "debug_gate_open", "knobs", "main",
           "overload_line", "problems", "read_global", "restart_due", "run"]
