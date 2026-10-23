"""The script DSL behind #23 auto script.

The reference page changes its buttons with the script picked in its combo box,
so the steps cannot live in the code: they are a YAML file under ``scripts/`` and
this module is the only place that reads and checks them.

A script::

    name: warm_up
    title: Warm up a chat
    steps:
      - action: log
        message: starting
      - action: join
        target: "@demo"
      - action: loop
        times: 2
        steps:
          - action: send_text
            text: "{{greeting}}"
          - action: wait
            seconds: 30

Every step may carry ``on_error: stop`` (the default) or ``on_error: continue``.
An unknown action, a missing field or a bad value is a :class:`ConfigError` that
names the file and the step index, because that is the only way a user can fix
it.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import ConfigError, load_yaml

#: ``action`` -> the fields it needs.  ``steps`` marks the two block actions.
ACTIONS: dict[str, tuple[str, ...]] = {
    "wait": ("seconds",),
    "send_text": ("text",),
    "send_media": ("path",),
    "join": ("target",),
    "leave": ("target",),
    "invite": ("target", "contact"),
    "contact_add": ("contact",),
    "set_profile": ("first",),
    "if": ("condition",),
    "loop": ("times", "steps"),
    "log": ("message",),
}

BLOCKS = ("if", "loop")
ON_ERROR = ("stop", "continue")
#: Fields every step type inherits; the rest are the ``steps`` of a block.
COMMON = ("action", "on_error", "comment", "then", "else")

_VAR = re.compile(r"\{\{\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_CONDITION = re.compile(
    r"^\s*(?P<field>[a-z_]+)\s*(?P<op>==|!=|>=|<=|>|<)\s*(?P<value>-?\d+)\s*$"
)
_FIELDS = ("ok", "failed", "skipped", "steps")


@dataclass(slots=True)
class Step:
    """One instruction, already validated, with its guard chain."""

    action: str
    params: dict[str, Any] = field(default_factory=dict)
    on_error: str = "stop"
    guards: list[tuple[str, bool]] = field(default_factory=list)

    def get(self, key: str, default: Any = "") -> Any:
        return self.params.get(key, default)


@dataclass(slots=True)
class Script:
    name: str
    title: str
    steps: list[dict[str, Any]]
    description: str = ""
    path: str = ""

    def describe(self) -> str:
        return f"{self.name}\t{self.title}\t{len(self.steps)} steps"


def _require(raw: dict[str, Any], keys: tuple[str, ...], where: str) -> None:
    missing = [key for key in keys if raw.get(key) in (None, "")]
    if missing:
        raise ConfigError(f"{where}: {raw.get('action')} needs {', '.join(missing)}")


def _check_step(raw: Any, where: str) -> None:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: a step must be a mapping, got {raw!r}")
    action = str(raw.get("action") or "")
    if action not in ACTIONS:
        known = ", ".join(sorted(ACTIONS))
        raise ConfigError(
            f"{where}: unknown action {action!r} (have: {known})"
        )
    _require(raw, ACTIONS[action], where)
    on_error = str(raw.get("on_error") or "stop")
    if on_error not in ON_ERROR:
        raise ConfigError(
            f"{where}: on_error must be one of {', '.join(ON_ERROR)}, "
            f"got {on_error!r}"
        )
    if action == "loop":
        times = raw.get("times")
        try:
            number = int(times)
        except (TypeError, ValueError) as error:
            raise ConfigError(f"{where}: loop times is not a number: {times!r}") from error
        if number < 1:
            raise ConfigError(f"{where}: loop times must be >= 1, got {number}")
        if number > 1000:
            raise ConfigError(f"{where}: loop times {number} is over the cap 1000")
    if action == "if":
        condition = str(raw.get("condition") or "")
        match = _CONDITION.match(condition)
        if not match:
            raise ConfigError(
                f"{where}: if condition {condition!r} must look like "
                f"'failed == 0' (fields: {', '.join(_FIELDS)})"
            )
        if match.group("field") not in _FIELDS:
            raise ConfigError(
                f"{where}: if condition field {match.group('field')!r} "
                f"is not one of {', '.join(_FIELDS)}"
            )
        if not raw.get("then"):
            raise ConfigError(f"{where}: if needs a then block")
    if action == "wait":
        try:
            seconds = float(raw.get("seconds"))
        except (TypeError, ValueError) as error:
            raise ConfigError(f"{where}: wait seconds is not a number") from error
        if seconds < 0:
            raise ConfigError(f"{where}: wait seconds must not be negative")
    if action in BLOCKS:
        children = raw.get("steps" if action == "loop" else "then")
        if not isinstance(children, list) or not children:
            raise ConfigError(f"{where}: {action} needs a non empty block")
        for index, child in enumerate(children, start=1):
            _check_step(child, f"{where}.{index}")


def check(steps: list[Any], where: str = "steps") -> None:
    for index, step in enumerate(steps, start=1):
        _check_step(step, f"{where}[{index}]")


def parse_condition(condition: str) -> tuple[str, str, int]:
    """``failed <= 0`` -> ``("failed", "<=", 0)``."""
    match = _CONDITION.match(condition)
    if not match:
        raise ConfigError(f"bad condition: {condition!r}")
    return match.group("field"), match.group("op"), int(match.group("value"))


def holds(condition: str, counters: dict[str, int]) -> bool:
    """Evaluate a condition against the run counters."""
    name, operator, value = parse_condition(condition)
    current = int(counters.get(name, 0))
    if operator == "==":
        return current == value
    if operator == "!=":
        return current != value
    if operator == ">=":
        return current >= value
    if operator == "<=":
        return current <= value
    if operator == ">":
        return current > value
    return current < value


def load(path: str | Path) -> Script:
    """Read and validate one script file."""
    target = Path(path).expanduser()
    raw = load_yaml(target)
    name = str(raw.get("name") or target.stem)
    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ConfigError(f"{target}: steps must be a non empty list")
    check(list(steps), f"{target}: steps")
    return Script(
        name=name,
        title=str(raw.get("title") or name),
        steps=list(steps),
        description=str(raw.get("description") or ""),
        path=str(target),
    )


def discover(folder: str | Path) -> list[Script]:
    """Every ``*.yaml`` under the folder that parses; a broken one is reported."""
    base = Path(folder).expanduser()
    if not base.is_dir():
        return []
    found: list[Script] = []
    for path in sorted(base.glob("*.yaml")):
        try:
            found.append(load(path))
        except ConfigError as error:
            print(f"skip {path.name}: {error}")
    return found


def find(folder: str | Path, name: str) -> Script:
    """A script by name, with a helpful list when it is not there."""
    scripts = discover(folder)
    matches = [item for item in scripts
               if item.name == name or Path(item.path).stem == name]
    if not matches:
        known = ", ".join(item.name for item in scripts) or "(none)"
        raise ConfigError(f"no script named {name!r} in {folder} (have: {known})")
    return matches[0]


def expand(steps: list[dict[str, Any]]) -> list[Step]:
    """Flatten loops and if branches so every step has a number up front.

    An ``if`` contributes both branches, each behind a guard; a branch that does
    not hold is skipped at run time instead of being counted differently.
    """
    plan: list[Step] = []

    def walk(items: list[dict[str, Any]],
             guards: list[tuple[str, bool]]) -> None:
        for raw in items:
            action = str(raw["action"])
            params = {key: value for key, value in raw.items()
                      if key not in COMMON and key != "steps"}
            on_error = str(raw.get("on_error") or "stop")
            if action == "loop":
                for _ in range(int(raw["times"])):
                    walk(list(raw.get("steps") or []), guards)
                continue
            if action == "if":
                condition = str(raw["condition"])
                walk(list(raw.get("then") or []), [*guards, (condition, True)])
                walk(list(raw.get("else") or []), [*guards, (condition, False)])
                continue
            plan.append(Step(action, copy.deepcopy(params), on_error,
                             list(guards)))

    walk(steps, [])
    return plan


def guard_holds(guards: list[tuple[str, bool]],
                counters: dict[str, int]) -> bool:
    """True when every guard on the way to a step holds."""
    return all(holds(condition, counters) is expect
               for condition, expect in guards)


def substitute(value: Any, variables: dict[str, str]) -> Any:
    """Fill ``{{name}}`` in a string; an unknown name is left visible."""
    if not isinstance(value, str) or "{{" not in value:
        return value

    def replace(match: re.Match[str]) -> str:
        return variables.get(match.group("name"), match.group(0))

    return _VAR.sub(replace, value)


def load_vars(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for entry in pairs or []:
        key, _, value = str(entry).partition("=")
        if not key.strip():
            raise ConfigError(f"--var needs name=value, got {entry!r}")
        out[key.strip()] = value.strip()
    return out


__all__ = [
    "ACTIONS",
    "BLOCKS",
    "ON_ERROR",
    "Script",
    "Step",
    "check",
    "discover",
    "expand",
    "find",
    "guard_holds",
    "holds",
    "load",
    "load_vars",
    "parse_condition",
    "substitute",
]
