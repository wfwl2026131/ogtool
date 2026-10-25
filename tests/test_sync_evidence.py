"""The note's generated blocks: each one stays inside its own markers.

The first version of the two-block sync looked up the module-level evidence
markers no matter which block it was writing, so the live table overwrote the
evidence table and the evidence markers disappeared. Nothing caught it until the
docs check learned that a note can *lose* its markers, which is what these tests
pin down.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load():
    path = ROOT / "scripts" / "sync_evidence.py"
    spec = importlib.util.spec_from_file_location("sync_evidence_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_each_block_replaces_only_itself():
    module = load()
    text = (f"{module.START}\nold evidence\n{module.END}\n"
            f"{module.LIVE_START}\nold live\n{module.LIVE_END}\n")
    # The real renderers hand back their block with the markers included, which
    # is what keeps the markers in the note after a rewrite.
    evidence_block = f"{module.START}\nNEW EVIDENCE\n{module.END}"
    live_block = f"{module.LIVE_START}\nNEW LIVE\n{module.LIVE_END}"
    once, found = module.replace_block(text, evidence_block, module.START,
                                       module.END)
    assert found
    twice, found = module.replace_block(once, live_block, module.LIVE_START,
                                        module.LIVE_END)
    assert found
    assert "NEW EVIDENCE" in twice and "NEW LIVE" in twice
    assert "old evidence" not in twice and "old live" not in twice
    assert twice.count(module.START) == 1 and twice.count(module.LIVE_START) == 1


def test_a_block_with_its_markers_gone_is_reported(tmp_path):
    module = load()
    text = f"{module.LIVE_START}\nx\n{module.LIVE_END}\n"
    _text, found = module.replace_block(text, "B", module.START, module.END)
    assert found is False
