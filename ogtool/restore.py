"""The restore-mode machine (RestoreManagerEx, unbound).

The reference reopens with a flag and picks its last task back up
(:1740190 ``In restore mode, auto login``, one ``start X from restore
mode by flag`` line per page: :1741844 puppet chat, :1744844 copy chat,
:1744967 ai chat, :1745088 ai comment, :1745148 ai clone).  The machine
speaks three sentences (:1744030, :1742974-1742975):

    Restore mode: %1
    Successfully restored task %1 execution state
    Failed to restore task %1 state, starting from beginning

and the settings page adds the timing knob (:1742796-1742797 ``Restore
delay``: extra delay on the auto-restore timers, 0 = none).

The facts a restore needs come from the receipt the task itself wrote:
the newest receipt of the feature is the task to restore, and its state
(decided/stopped, planned rows, config) is what the new run resumes
from.  A receipt that carries no resumable state is a failure line, and
the run starts from the beginning - the reference's own verdict, not a
crash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import receipts

#: The machine's own sentences (:1744030, :1742974-1742975).
LINE_MODE = "Restore mode: %1"
LINE_OK = "Successfully restored task %1 execution state"
LINE_FAILED = "Failed to restore task %1 state, starting from beginning"

#: Features a restore may pick back up, the pages with their own
#: ``start ... from restore mode by flag`` line.
RESTORABLE = (
    "puppet_chat",
    "copy_chat",
    "ai_chat",
    "ai_comment",
    "ai_clone",
    "batch_join",
    "batch_invite_v2",
    "mass_send_v2",
    "watch_chat",
)


@dataclass(slots=True)
class RestorePlan:
    """What the machine decided for one feature."""

    feature: str
    ok: bool
    line: str
    task_id: str = ""
    config: dict[str, Any] = field(default_factory=dict)


def restore_line(mode: str) -> str:
    """``Restore mode: %1`` (:1744030) - the flag the run reopened with."""
    return LINE_MODE.replace("%1", mode)


def _resumable(body: dict[str, Any]) -> bool:
    """Whether a receipt carries state worth resuming.

    A task with a planned row count above zero and a config block is
    resumable; an empty plan or a receipt without a task id is not.
    """
    task_id = str(body.get("task_id") or "")
    if not task_id:
        return False
    planned = body.get("planned")
    try:
        if int(planned or 0) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    knobs = body.get("knobs")
    return isinstance(knobs, dict) and bool(knobs)


def plan_restore(settings: Any, feature: str, delay_sec: float = 0.0) -> RestorePlan:
    """The machine's decision for one feature, as its own line.

    The newest receipt of the feature is the candidate (:1742974); a
    feature with no receipt, or one without resumable state, answers the
    failure line (:1742975) - starting from the beginning is the machine's
    documented fallback, not an error.  The delay knob rides the line as
    the settings page documents it (:1742797).
    """
    directory = receipts.receipt_dir(settings)
    bodies, _problems = receipts.read_all(directory)
    newest: dict[str, Any] | None = None
    for body in bodies:
        if str(body.get("feature") or "") != feature:
            continue
        if newest is None or str(body.get("task_id")) > str(newest.get("task_id")):
            newest = body
    if newest is None:
        return RestorePlan(
            feature=feature,
            ok=False,
            line=LINE_FAILED.replace("%1", feature),
        )
    if not _resumable(newest):
        return RestorePlan(
            feature=feature,
            ok=False,
            line=LINE_FAILED.replace("%1", feature),
            task_id=str(newest.get("task_id") or ""),
        )
    suffix = f" (delay {delay_sec:g}s)" if delay_sec else ""
    return RestorePlan(
        feature=feature,
        ok=True,
        line=LINE_OK.replace("%1", str(newest.get("task_id"))) + suffix,
        task_id=str(newest.get("task_id") or ""),
        config=dict(newest.get("knobs") or {}),
    )


def plan_all(settings: Any, delay_sec: float = 0.0) -> list[RestorePlan]:
    """The whole reopening sweep, one plan per restorable feature."""
    return [plan_restore(settings, feature, delay_sec) for feature in RESTORABLE]


__all__ = [
    "LINE_FAILED",
    "LINE_MODE",
    "LINE_OK",
    "RESTORABLE",
    "RestorePlan",
    "plan_all",
    "plan_restore",
    "restore_line",
]
