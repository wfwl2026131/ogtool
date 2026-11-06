"""The auto-reply rules page: Keyword / Reply Content / Add Rule.

The page's own sentences (:1741027-1741048): the two refusals, the
first-time default, add / delete / batch delete / update / rule state and
the duplicate-interval setter, all against tdata/auto_reply_config.json.
"""

from __future__ import annotations

import json

from ogtool.features.auto_reply import (
    AutoReplyConfig,
    RuleStore,
    _edit_rules,
    run,
)


def _config(**overrides) -> AutoReplyConfig:
    values = {"rules_file": ""}
    values.update(overrides)
    return AutoReplyConfig(**values)


def test_a_first_time_load_initializes_the_default(tmp_path, capsys):
    store = RuleStore(tmp_path / "auto_reply_config.json")
    data = store.load()
    assert data == {"enabled": True, "duplicate_seconds": 60, "rules": []}
    assert (
        "Initialized default auto-reply rule for first-time user"
        in capsys.readouterr().out
    )


def test_add_uses_the_pages_refusals_and_sentence(tmp_path, capsys):
    store = RuleStore(tmp_path / "c.json")
    store.load()
    assert not store.add("=>reply")
    assert "No keyword" in capsys.readouterr().out
    assert not store.add("hello=>")
    assert "No content" in capsys.readouterr().out
    assert store.add("hello=>hi there")
    assert "Added new rule: rule1" in capsys.readouterr().out
    assert store.data["rules"][0]["replyContent"] == "hi there"


def test_delete_single_batch_and_none(tmp_path, capsys):
    store = RuleStore(tmp_path / "c.json")
    store.load()
    store.add("a=>1")
    store.add("b=>2")
    assert not store.delete([])
    assert "No rules selected for deletion" in capsys.readouterr().out
    assert store.delete(["rule1"])
    assert "Deleted rule: rule1" in capsys.readouterr().out
    assert store.delete(["rule2", "missing"])
    assert "Batch deleted 1 rules" in capsys.readouterr().out


def test_update_and_state_name_the_rule(tmp_path, capsys):
    store = RuleStore(tmp_path / "c.json")
    store.load()
    store.add("a=>1")
    assert store.update("rule1=>b=>2")
    assert "Updated rule: rule1" in capsys.readouterr().out
    assert store.state("rule1=>disabled")
    assert "Rule rule1 disabled" in capsys.readouterr().out
    assert store.data["rules"][0]["enabled"] is False


def test_interval_uses_the_pages_setter(tmp_path, capsys):
    store = RuleStore(tmp_path / "c.json")
    store.load()
    store.interval(30)
    assert "Set auto reply duplicate interval to 30 seconds" in capsys.readouterr().out
    assert store.data["duplicate_seconds"] == 30


def test_an_edit_run_writes_the_page_file(tmp_path, capsys):
    path = tmp_path / "tdata" / "auto_reply_config.json"
    config = _config(
        rules_file=str(path),
        rule_add=["hello=>hi"],
        duplicate_interval=15,
    )
    assert _edit_rules(config) == 0
    out = capsys.readouterr().out
    assert "Added new rule: rule1" in out
    assert "Set auto reply duplicate interval to 15 seconds" in out
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["rules"][0]["keyword"] == "hello"
    assert saved["duplicate_seconds"] == 15


def test_a_failed_edit_does_not_write(tmp_path):
    path = tmp_path / "c.json"
    config = _config(rules_file=str(path), rule_add=["=>nothing"])
    assert _edit_rules(config) == 1
    assert not path.exists()


def test_editing_short_circuits_the_listening_run(ctx, tmp_path, capsys):
    config = _config(rules_file=str(tmp_path / "c.json"), rules_list=True)
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "Auto reply True" in out
    assert "Duplicate Cache(Seconds): 60" in out
    assert "task:" not in out


def test_the_page_flags_are_wired_into_the_cli(tmp_path):
    from ogtool.cli import build_parser
    from ogtool.features.auto_reply import FLAGS
    from ogtool.flags import section

    path = tmp_path / "c.json"
    args = build_parser().parse_args(
        ["auto_reply", "--rule-add", "hello=>hi", "--rules-file", str(path)]
    )
    values = section(args, FLAGS)
    assert values["rule_add"] == ["hello=>hi"]
    assert values["rules_file"] == str(path)
    config = AutoReplyConfig.from_dict({"auto_reply": values})
    assert config.editing
    assert _edit_rules(config) == 0
    assert (
        json.loads(path.read_text(encoding="utf-8"))["rules"][0]["keyword"] == "hello"
    )
