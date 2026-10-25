"""The audit script itself: a knob that is only printed must be reported."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "scripts" / "knob_audit.py"


def load():
    spec = importlib.util.spec_from_file_location("knob_audit", SRC)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_a_field_only_printed_in_the_header_is_dead(tmp_path):
    module = load()
    path = tmp_path / "demo.py"
    path.write_text(
        "from ..flags import Flag\n"
        "\n"
        "class DemoConfig:\n"
        "    printed_only: int = 0\n"
        "    decided: int = 0\n"
        "\n"
        "    @classmethod\n"
        "    def from_dict(cls, raw):\n"
        "        return cls(printed_only=raw.get('printed_only', 0),\n"
        "                   decided=raw.get('decided', 0))\n"
        "\n"
        "def run(config, ctx):\n"
        "    header(total=config.printed_only)\n"
        "    if config.decided:\n"
        "        print('used')\n",
        encoding="utf-8",
    )
    assert module.audit(path) == ["printed_only"]


def test_a_pass_through_into_the_constructor_is_not_use(tmp_path):
    module = load()
    path = tmp_path / "demo.py"
    path.write_text(
        "class DemoConfig:\n"
        "    passthrough: int = 0\n"
        "\n"
        "    @classmethod\n"
        "    def from_dict(cls, raw):\n"
        "        return cls(passthrough=raw.get('passthrough', 0))\n"
        "\n"
        "def run(config, ctx):\n"
        "    header(a=1)\n",
        encoding="utf-8",
    )
    assert module.audit(path) == ["passthrough"]


def test_a_key_read_in_from_dict_logic_counts_as_use(tmp_path):
    module = load()
    path = tmp_path / "demo.py"
    path.write_text(
        "class DemoConfig:\n"
        "    mode: str = ''\n"
        "\n"
        "    @classmethod\n"
        "    def from_dict(cls, raw):\n"
        "        mode = str(raw.get('mode') or '').lower()\n"
        "        return cls(mode=mode)\n",
        encoding="utf-8",
    )
    assert module.audit(path) == []


def test_every_dead_knob_in_the_repo_has_a_decision():
    """No knob may be dead *and* unexamined: that is how drift comes back."""
    module = load()
    results = module.main([])
    assert results == 0


def test_a_blank_reason_is_a_problem():
    """'not used' is not a decision; the table has to say what was decided."""
    module = load()
    found = module.problems_in_known({"a.b": ""})
    assert found and "reason" in found[0]


def test_a_short_reason_is_a_problem():
    module = load()
    found = module.problems_in_known({"a.b": "unused"})
    assert found and "characters" in found[0]


def test_a_malformed_key_is_a_problem():
    module = load()
    found = module.problems_in_known({"a": "x" * 40})
    assert found and "feature.field" in found[0]


def test_the_real_table_passes_its_own_check():
    module = load()
    assert module.problems_in_known(module.KNOWN) == []
