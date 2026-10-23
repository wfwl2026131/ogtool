"""Helpers shared by the feature modules.

The pages differ in their fields, not in their plumbing: read a section out of
the task YAML, coerce a few numbers and lists, collect targets and accounts,
then open and close one task row.  That plumbing lives here so each page file
stays about its own subject.
"""

from __future__ import annotations

from typing import Any

from ..config import ConfigError, Settings
from ..report import dedupe, read_lines
from ..runtime import RunContext
from ..store import TaskRef
from ..targets import Target, collect


def section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    """One named block of the task YAML, or an empty one."""
    value = raw.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{name}: the section must be a mapping")
    return value


def text(data: dict[str, Any], key: str, default: str = "") -> str:
    value = data.get(key, default)
    return "" if value is None else str(value)


def num(data: dict[str, Any], key: str, default: float,
        *, kind: type = float, minimum: float | None = None) -> Any:
    value = data.get(key)
    if value is None or value == "":
        result = kind(default)
    else:
        try:
            result = kind(float(value)) if kind is int else kind(value)
        except (TypeError, ValueError) as error:
            raise ConfigError(f"{key}: not a number: {value!r}") from error
    if minimum is not None and result < minimum:
        result = kind(minimum)
    return result


def lines(data: dict[str, Any], key: str) -> list[str]:
    """A list field, given either as a YAML list or as one blob of text.

    The pages take ``排除关键词（逗号或换行）`` and ``每行一个`` in the same
    box, so both spellings have to work.
    """
    value = data.get(key)
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        items = str(value).replace(",", "\n").splitlines()
    return dedupe(items)


def targets_of(data: dict[str, Any], key: str = "targets",
               kind: str = "custom") -> list[Target]:
    """Targets from ``inline``/``files``/``file`` in one section."""
    inline = data.get(key) or data.get("inline")
    if isinstance(inline, (list, tuple)):
        inline = ",".join(str(item) for item in inline)
    files = data.get("files") or data.get("file") or []
    if isinstance(files, str):
        files = [files]
    return collect(inline=str(inline) if inline else None,
                   files=list(files), kind=str(data.get("kind") or kind))


def accounts_of(raw: dict[str, Any], data: dict[str, Any] | None = None) -> list[str]:
    """The labels a task binds, from the top level or its own section."""
    value = (data or {}).get("accounts")
    if value is None:
        value = raw.get("accounts")
    if value is None:
        return []
    if isinstance(value, str):
        return dedupe(value.replace(",", "\n").splitlines())
    return dedupe([str(item) for item in value])


def name_of(raw: dict[str, Any], data: dict[str, Any] | None,
            fallback: str) -> str:
    return str((data or {}).get("task_name") or raw.get("task_name") or fallback)


def out_dir(ctx: RunContext, raw: dict[str, Any], settings: Settings) -> str:
    """Where a page writes its exports: the task directory by default."""
    named = str(raw.get("output_dir") or "").strip()
    if named:
        return named
    return str(settings.log_dir / "exports")


def open_task(ctx: RunContext, name: str, feature: str,
              config: dict[str, Any], planned: int) -> TaskRef:
    task = ctx.store.create_task(
        name, feature,
        config=config,
        mode="live" if ctx.live else "dry-run",
        planned=planned,
    )
    ctx.task_id = task.id
    return task


def close_task(ctx: RunContext, task: TaskRef, *, ok: int, failed: int,
               status: str = "") -> None:
    final = status or ("dry-run" if not ctx.live else "done")
    ctx.store.finish_task(task.id, sent=ok, failed=failed, status=final)


def record(ctx: RunContext, task: TaskRef, account: str, subject: str,
           ok: bool, code: str = "", detail: str = "") -> None:
    status = "sent" if ok else "failed"
    if not ctx.live and ok:
        status = "planned"
    ctx.store.add_result(task.id, account, subject, status, code, detail)


def blocks(data: dict[str, Any], key: str) -> list[str]:
    """A text box whose blank lines separate entries, each block kept whole."""
    value = data.get(key)
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [" ".join(str(item).split()) for item in value if str(item).strip()]
    out: list[str] = []
    current: list[str] = []
    for line in str(value).splitlines():
        if not line.strip():
            if current:
                out.append(" ".join(current))
                current = []
            continue
        current.append(line.strip())
    if current:
        out.append(" ".join(current))
    return [item for item in out if item]


def ai_client(ctx: RunContext, *, transport: object | None = None):
    """The model client for a task; a transport turns it into a mock."""
    from ..ai import AIClient

    if transport is None and ctx.ai is not None:
        return ctx.ai
    return AIClient(ctx.settings.ai, transport=transport)  # type: ignore[arg-type]


def record_ai(ctx: RunContext, task: TaskRef, account: str, kind: str,
              result: Any) -> None:
    """Persist one model call, so the audit trail survives the run."""
    ctx.store.add_ai_call(
        task.id,
        account=account,
        kind=kind,
        prompt=result.prompt,
        response=result.response,
        tokens=result.tokens,
        elapsed_ms=result.elapsed_ms,
        ok=result.ok,
        error=result.error,
    )


def read_list_file(path: str) -> list[str]:
    """A .txt the page asks the user to pick; ``#`` lines are comments."""
    return read_lines(path)


def result_code(ok: bool, code: str) -> str:
    return "ok" if ok else (code or "failed")


def header(title: str, **fields: Any) -> None:
    """``task: x`` then one ``key: value`` line per field, in page order."""
    print(f"task: {title}")
    for key, value in fields.items():
        label = key.replace("_", " ")
        print(f"{label}: {value}")
