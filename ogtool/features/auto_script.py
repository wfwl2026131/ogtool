"""#23 auto script - run a YAML script of primitive steps.

The reference page changes its buttons with the script selected in its combo
box, so nothing about one particular workflow belongs in the code: the steps
live in ``scripts/*.yaml`` and :mod:`ogtool.scriptdsl` is the only reader.  This
module turns a script into transport calls, numbers every step, and applies
``on_error`` per step.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import scriptdsl
from ..config import ConfigError, Settings
from ..flags import Flag
from ..runtime import RunContext
from ..senders import Content
from ..targets import TargetError, parse_many
from . import register
from ._common import close_task, header, name_of, open_task, section, text

FEATURE = "auto_script"
SECTION = "auto_script"
TITLE = "#23 自动脚本"

FLAGS = (
    Flag("--list", "list_scripts", str, "list the available scripts",
         action="store_true"),
    Flag("--script", "script_name", str, "script to run"),
    Flag("--script-dir", "script_dir", str, "where the scripts live"),
    Flag("--target", "target", str, "default target for the steps"),
    Flag("--var", "var", str, "text variable: name=value (repeatable)",
         action="append"),
    Flag("--on-error", "on_error", str,
         "override the per step policy: stop or continue"),
)

#: Where scripts live when the task file names no folder.
DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


@dataclass(slots=True)
class ScriptConfig:
    account_labels: list[str] = field(default_factory=list)
    script_name: str = ""
    list_only: bool = False
    script_dir: str = ""
    target: str = ""
    variables: dict[str, str] = field(default_factory=dict)
    on_error: str = ""
    task_name: str = "auto-script"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> ScriptConfig:
        data = section(raw, SECTION)
        directory = text(data, "script_dir") or _default_dir(settings)
        return cls(
            account_labels=list(raw.get("accounts") or []),
            script_name=text(data, "script_name"),
            list_only=bool(data.get("list_scripts")),
            script_dir=directory,
            target=text(data, "target"),
            variables=scriptdsl.load_vars(_as_list(data.get("var"))),
            on_error=text(data, "on_error"),
            task_name=name_of(raw, data, "auto-script"),
        )


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [part.strip() for part in str(value).splitlines() if part.strip()]


def _default_dir(settings: Settings) -> str:
    """``<data_dir>/scripts`` when it exists, else the one next to the package."""
    candidate = Path(settings.data_dir) / "scripts"
    if candidate.is_dir():
        return str(candidate)
    return str(DEFAULT_DIR)


def list_scripts(folder: str) -> int:
    scripts = scriptdsl.discover(folder)
    if not scripts:
        print(f"no scripts in {folder} (put a *.yaml with a steps list there)")
        return 0
    print(f"scripts in {folder}:")
    for script in scripts:
        print(f"  {script.describe()}")
        if script.description:
            print(f"    {script.description}")
    return 0


def _target_of(config: ScriptConfig, step: scriptdsl.Step) -> str:
    return str(step.get("target") or config.target or "")


def execute(config: ScriptConfig, ctx: RunContext,
            script: scriptdsl.Script) -> int:
    plan = scriptdsl.expand(script.steps)
    variables = dict(config.variables)
    sender = ctx.sender()
    account = (ctx.bind(config.account_labels) or [None])[0]
    label = account.label if account else "-"
    counters = {"ok": 0, "failed": 0, "skipped": 0, "steps": 0}
    stopped = ""

    for index, step in enumerate(plan, start=1):
        if not scriptdsl.guard_holds(step.guards, counters):
            counters["skipped"] += 1
            continue
        counters["steps"] += 1
        policy = config.on_error or step.on_error or "stop"
        values = {**variables, **{key: str(value)
                                  for key, value in counters.items()}}
        ok, detail = _perform(ctx, step, sender, label, config, values)
        if ok:
            counters["ok"] += 1
        else:
            counters["failed"] += 1
        print(f"[{index:03d}/{len(plan):03d}] step={step.action} "
              f"account={label} {detail} -> {'ok' if ok else 'failed'}")
        if not ok and policy == "stop":
            stopped = f"stopped at step {index} ({step.action})"
            print(f"stop: {stopped}")
            break
        if step.action == "wait" and not ctx.live:
            print(f"  dry-run: would wait {float(step.get('seconds', 0)):.0f}s")

    print(f"steps: {counters['steps']} / {len(plan)}")
    print(f"ok: {counters['ok']}")
    print(f"failed: {counters['failed']}")
    print(f"skipped: {counters['skipped']}")
    print(f"PLAN: {len(plan)} steps, script {script.name}")
    return 0 if not counters["failed"] else 1


def _perform(ctx: RunContext, step: scriptdsl.Step, sender, account: str,
             config: ScriptConfig,
             variables: dict[str, str]) -> tuple[bool, str]:
    """Run one step; returns (ok, the line the report shows)."""
    action = step.action
    params = {key: scriptdsl.substitute(value, variables)
              for key, value in step.params.items()}

    if action == "log":
        print(f"  log: {params.get('message', '')}")
        return True, f"message={params.get('message', '')}"
    if action == "wait":
        seconds = float(params.get("seconds", 0) or 0)
        if ctx.live and seconds > 0:
            time.sleep(seconds)
        return True, f"seconds={seconds:.0f}"
    if action in ("send_text", "send_media"):
        target_ref = _target_of(config, step)
        if not target_ref:
            return False, "target=- (no target given)"
        try:
            target = parse_many([str(target_ref)], "custom")[0]
        except TargetError as error:
            return False, f"target={target_ref} ({error})"
        content = (Content(text=str(params.get("text", "")))
                   if action == "send_text"
                   else Content(media=str(params.get("path", ""))))
        answer = sender.send(account, target, content)
        return answer.ok, (f"target={target.display()} "
                           f"content={content.describe()}")
    if action in ("join", "leave"):
        target_ref = _target_of(config, step)
        if not target_ref:
            return False, "target=- (no target given)"
        try:
            target = parse_many([str(target_ref)], "group")[0]
        except TargetError as error:
            return False, f"target={target_ref} ({error})"
        answer = (sender.join(account, target) if action == "join"
                  else sender.leave(account, target))
        return answer.ok, f"target={target.display()}"
    if action == "invite":
        try:
            group = parse_many([str(params.get("target"))], "group")[0]
            contact = parse_many([str(params.get("contact"))], "custom")[0]
        except TargetError as error:
            return False, f"target={params.get('target')} ({error})"
        answer = sender.invite(account, group, contact)
        return answer.ok, f"group={group.display()} contact={contact.display()}"
    if action == "contact_add":
        try:
            contact = parse_many([str(params.get("contact"))], "custom")[0]
        except TargetError as error:
            return False, f"contact={params.get('contact')} ({error})"
        answer = sender.add_contact(account, contact)
        return answer.ok, f"contact={contact.display()}"
    if action == "set_profile":
        first = str(params.get("first", ""))
        last = str(params.get("last", ""))
        answer = sender.update_profile(account, first, last)
        return answer.ok, f"first={first or '-'} last={last or '-'}"
    return False, "unknown action"


def run(config: ScriptConfig, ctx: RunContext) -> int:
    if not config.script_name:
        print("ERROR: no script given (--script name, or --list)")
        return 2
    if config.on_error and config.on_error not in scriptdsl.ON_ERROR:
        raise ConfigError(
            f"--on-error must be one of {', '.join(scriptdsl.ON_ERROR)}"
        )
    script = scriptdsl.find(config.script_dir, config.script_name)
    plan = scriptdsl.expand(script.steps)

    header(
        config.task_name,
        script=script.name,
        script_title=script.title,
        source=script.path,
        steps=len(plan),
        target=config.target or "-",
        variables=len(config.variables) or "-",
    )
    task = open_task(ctx, config.task_name, FEATURE,
                     {"script": script.path, "steps": len(plan)}, len(plan))
    ctx.task_id = task.id
    code = execute(config, ctx, script)
    close_task(ctx, task, ok=0, failed=0)
    return code


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    config = ScriptConfig.from_dict(raw, ctx.settings)
    if config.list_only or not config.script_name:
        return list_scripts(config.script_dir)
    return run(config, ctx)


__all__ = ["DEFAULT_DIR", "ScriptConfig", "TITLE", "execute", "list_scripts",
           "main", "run"]
