"""Find knobs the reference page has and the run does not use.

A Config field is *dead* when the only place it appears is the dataclass
declaration and ``header(...)``: it is read from the task file, printed in the
summary, and never consulted. This is the mechanical version of "该字段只打印
不使用" - the check that turns the hidden-mechanism hunt from memory into a list.

    python scripts/knob_audit.py            # every dead knob
    python scripts/knob_audit.py --count    # just the total

Rules, learned the hard way:
  * a field read inside ``from_dict`` (which reads ``data``, never ``config``) is
    NOT dead - ``num(data, "x")`` / ``data.get("x")`` count as use;
  * a plain pass-through into ``cls(...)`` is NOT use: every field appears there,
    so counting it would mark the whole file clean. Only a mention outside that
    constructor call counts;
  * a field used only inside ``header(...)`` IS dead: printing is not deciding.

Exit code is 0 either way; the list is the deliverable.

``KNOWN`` records the fields this project has looked at and decided about, with
the reason. They stay in the report - a knob that is deliberately unwired is
different from one nobody noticed - but they no longer need re-litigating every
run.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys

#: ``feature.field`` -> why it is not wired, decided once, kept visible.
KNOWN = {
    "ai_chat.sticker_send_per_mille":
        "needs a sticker pool; this page carries none and Content.sticker "
        "takes a path",
    "ai_comment.sticker_send_percent": "same sticker pool problem as ai_chat",
    "ai_clone.passive":
        "the reference's own long-running follow mode; this run is one-shot",
    "ai_clone.last_source_message_id":
        "the reference's resumable pair watermark, not a page option",
    "ai_clone.total_cloned": "a counter the reference persists, not an option",
    "ai_clone.only_root_forwarded":
        "the key exists but nothing in the dump shows what 'root' means here",
    "ai_comment.only_root_forwarded": "same unproven semantics as ai_clone",
    "batch_login.account_labels":
        "consumed through accounts_of(raw, data) - a heuristic false positive",
    "batch_login.hide_import_dialog":
        "a GUI preference: there is no import dialog to hide in a CLI",
    "batch_login.import_session_on_drop":
        "a drag-and-drop behaviour of the reference's window, no CLI equivalent",
    "mass_send.target_kind":
        "set by the CLI override layer, not by the task file",
    "mass_send.dry_run": "set by the CLI (not ctx.live), not by the task file",
    "proxy.task_name": "consumed through name_of(raw, data) - false positive",
    "settings.raw": "the base class's own raw dict, read by subclasses",
}


def config_fields(tree: ast.Module) -> list[str]:
    fields: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.endswith("Config"):
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and \
                        isinstance(item.target, ast.Name):
                    fields.append(item.target.id)
    return fields


def header_lines(tree: ast.Module) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "header":
            for sub in ast.walk(node):
                if hasattr(sub, "lineno"):
                    lines.add(sub.lineno)
    return lines


def cls_lines(tree: ast.Module) -> set[int]:
    """Lines of every ``cls(...)`` / ``SomeConfig(...)`` constructor call."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else ""
        if name == "cls" or name.endswith("Config"):
            for sub in ast.walk(node):
                if hasattr(sub, "lineno"):
                    lines.add(sub.lineno)
    return lines


def audit(path: pathlib.Path) -> list[str]:
    src = path.read_text()
    tree = ast.parse(src)
    fields = config_fields(tree)
    if not fields:
        return []
    printed = header_lines(tree)
    constructed = cls_lines(tree)
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "config" and node.lineno not in printed:
            used.add(node.attr)
    dead = []
    for name in fields:
        if name in used:
            continue
        # from_dict consumption: the key used somewhere other than as a raw
        # pass-through into the constructor.
        key_lines = [
            number for number, line in enumerate(src.splitlines(), start=1)
            if re.search(rf'["\']{name}["\']', line)
        ]
        if any(number not in constructed for number in key_lines):
            continue
        dead.append(name)
    return dead


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", default="ogtool/features")
    parser.add_argument("--count", action="store_true",
                        help="print only the total")
    parser.add_argument("--known", action="store_true",
                        help="list the decided knobs and their reasons")
    args = parser.parse_args(argv)
    root = pathlib.Path(args.path)
    total = 0
    undecided = 0
    for path in sorted(root.glob("*.py")):
        dead = audit(path)
        if not dead:
            continue
        total += len(dead)
        stem = path.stem
        if not args.count:
            print(f"{path.name}: {', '.join(dead)}")
            if args.known:
                for name in dead:
                    reason = KNOWN.get(f"{stem}.{name}")
                    if reason:
                        print(f"  {name}: {reason}")
        undecided += sum(1 for name in dead
                         if f"{stem}.{name}" not in KNOWN)
    print(f"dead: {total} (undecided: {undecided})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
