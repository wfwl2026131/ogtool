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
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys


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
    args = parser.parse_args(argv)
    root = pathlib.Path(args.path)
    total = 0
    for path in sorted(root.glob("*.py")):
        dead = audit(path)
        if not dead:
            continue
        total += len(dead)
        if not args.count:
            print(f"{path.name}: {', '.join(dead)}")
    print(f"dead: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
