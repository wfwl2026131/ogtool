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

from ..flags import Flag
from ..runtime import RunContext
from . import register

FEATURE = "settings"

FLAGS = (
    Flag("--json", "json", None, "print the knobs as JSON", action="store_true"),
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


@dataclass(slots=True)
class SettingsPageConfig:
    """The page has no inputs of its own: it reflects the task YAML."""

    as_json: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict) -> SettingsPageConfig:
        page = raw.get("settings") or {}
        return cls(as_json=bool(page.get("json")), raw=raw)


def knobs(runtime: Any) -> list[tuple[str, str, Any]]:
    """The five numeric knobs, in the order the page shows them."""
    return [(key, label, getattr(runtime, key, 0)) for key, label, _ in KNOBS]


def problems(runtime: Any) -> list[str]:
    """Every knob that cannot be used as it stands, with its rule."""
    found: list[str] = []
    for key, label, rule in KNOBS:
        value = getattr(runtime, key, 0)
        if rule == "positive" and value <= 0:
            found.append(f"{key} ({label}) must be above 0, is {value}")
        elif rule == "non_negative" and value < 0:
            found.append(f"{key} ({label}) must not be negative, is {value}")
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
    found = problems(runtime)

    if config.as_json:
        # The flat object is the shape the page has always emitted, one key per
        # knob; the problems go to stderr so a pipe stays parseable.
        print(json.dumps(
            {key: value for key, _label, value in rows},
            ensure_ascii=False,
            indent=2,
        ))
        for line in found:
            print(f"PROBLEM: {line}", file=sys.stderr)
        return 1 if found else 0

    print(f"config: {ctx.settings.path}")
    print("runtime settings (no licence keys, restart is config only):")
    for key, label, value in rows:
        print(f"  {key:<18} {label:<14} {value}")
    print(f"restart due at: {restart_due(runtime)}")
    print("overload check: "
          f"{'off' if runtime.tp_overload <= 0 else runtime.tp_overload}")
    for line in found:
        print(f"  PROBLEM: {line}")
    return 1 if found else 0


@register(FEATURE, "settings")
def main(raw: dict, ctx: RunContext) -> int:
    return run(SettingsPageConfig.from_dict(raw), ctx)


__all__ = ["FEATURE", "FLAGS", "KNOBS", "SettingsPageConfig", "knobs", "main",
           "overload_line", "problems", "restart_due", "run"]
