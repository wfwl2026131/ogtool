"""#23 auto script: the DSL, its errors, and the two on_error branches."""

from __future__ import annotations

from pathlib import Path

import pytest

from ogtool import scriptdsl
from ogtool.config import ConfigError
from ogtool.features import auto_script
from ogtool.features.auto_script import ScriptConfig, execute, list_scripts
from ogtool.scriptdsl import Script as DslScript
from ogtool.scriptdsl import Step, expand, holds, parse_condition, substitute
from ogtool.simulate import Script

THREE_SENDS = """\
name: stop_me
title: Stop me
steps:
  - action: send_text
    text: first
  - action: send_text
    text: second
    on_error: continue
  - action: send_text
    text: third
"""


def write_script(tmp_path: Path, body: str, name: str = "demo.yaml") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def config_for(tmp_path: Path, **overrides) -> ScriptConfig:
    values = {
        "account_labels": ["acc1"],
        "script_name": "stop_me",
        "script_dir": str(tmp_path),
        "target": "@demo",
    }
    values.update(overrides)
    return ScriptConfig(**values)


def test_an_unknown_action_names_the_file_and_the_step(tmp_path, capsys):
    path = write_script(tmp_path, """\
name: broken
steps:
  - action: log
    message: fine
  - action: teleport
    where: mars
""")
    with pytest.raises(ConfigError) as caught:
        scriptdsl.load(path)
    message = str(caught.value)
    assert str(path) in message
    assert "steps[2]" in message
    assert "unknown action 'teleport'" in message
    # The error lists what is allowed, so the user can fix it without the docs.
    assert "send_text" in message


def test_a_missing_field_is_reported(tmp_path):
    path = write_script(tmp_path, """\
name: broken
steps:
  - action: invite
    target: "@group"
""")
    with pytest.raises(ConfigError) as caught:
        scriptdsl.load(path)
    message = str(caught.value)
    assert "steps[1]" in message
    assert "invite needs contact" in message


def test_a_bad_if_condition_is_reported(tmp_path):
    path = write_script(tmp_path, """\
name: broken
steps:
  - action: if
    condition: failed = 0
    then:
      - action: log
        message: never
""")
    with pytest.raises(ConfigError) as caught:
        scriptdsl.load(path)
    message = str(caught.value)
    assert "must look like 'failed == 0'" in message
    assert "ok" in message and "skipped" in message


def test_an_unknown_if_field_is_reported(tmp_path):
    path = write_script(tmp_path, """\
name: broken
steps:
  - action: if
    condition: errors == 0
    then:
      - action: log
        message: never
""")
    with pytest.raises(ConfigError) as caught:
        scriptdsl.load(path)
    assert "condition field 'errors'" in str(caught.value)


def test_loop_times_must_be_a_number(tmp_path):
    path = write_script(tmp_path, """\
name: broken
steps:
  - action: loop
    times: many
    steps:
      - action: log
        message: once
""")
    with pytest.raises(ConfigError) as caught:
        scriptdsl.load(path)
    assert "loop times is not a number" in str(caught.value)


def test_loop_times_must_be_within_the_cap(tmp_path):
    path = write_script(tmp_path, """\
name: broken
steps:
  - action: loop
    times: 5000
    steps:
      - action: log
        message: once
""")
    with pytest.raises(ConfigError) as caught:
        scriptdsl.load(path)
    assert "over the cap 1000" in str(caught.value)


def test_expand_flattens_loops_and_guards_both_branches():
    steps = [
        {"action": "log", "message": "start"},
        {"action": "loop", "times": 2,
         "steps": [{"action": "log", "message": "inside"}]},
        {"action": "if", "condition": "failed == 0",
         "then": [{"action": "log", "message": "yes"}],
         "else": [{"action": "log", "message": "no"}]},
    ]
    plan = expand(steps)
    assert [step.action for step in plan] == ["log", "log", "log", "log", "log"]
    assert plan[0].guards == []
    assert plan[1].get("message") == "inside"
    assert plan[3].guards == [("failed == 0", True)]
    assert plan[4].get("message") == "no"
    assert plan[4].guards == [("failed == 0", False)]
    assert all(isinstance(step, Step) for step in plan)


def test_holds_reads_the_counters_and_parse_condition_splits():
    assert parse_condition("failed <= 0") == ("failed", "<=", 0)
    assert holds("failed == 0", {"failed": 0})
    assert not holds("failed == 0", {"failed": 2})
    assert holds("ok >= 3", {"ok": 3})
    assert holds("skipped != 1", {"skipped": 0})
    with pytest.raises(ConfigError):
        parse_condition("when ok is fine")


def test_substitute_fills_known_names_and_keeps_unknown_ones():
    assert substitute("hi {{name}}", {"name": "Ann"}) == "hi Ann"
    assert substitute("hi {{ name }}", {"name": "Ann"}) == "hi Ann"
    assert substitute("hi {{nope}}", {}) == "hi {{nope}}"
    assert substitute(7, {}) == 7
    assert scriptdsl.load_vars(["a=1", " b = two "]) == {"a": "1", "b": "two"}
    with pytest.raises(ConfigError):
        scriptdsl.load_vars(["=x"])


def test_find_reports_the_scripts_it_does_have(tmp_path):
    write_script(tmp_path, THREE_SENDS)
    with pytest.raises(ConfigError) as caught:
        scriptdsl.find(tmp_path, "nope")
    assert "have: stop_me" in str(caught.value)


def test_list_prints_each_script_with_its_step_count(tmp_path, capsys):
    write_script(tmp_path, THREE_SENDS)
    write_script(tmp_path, """\
name: short
title: Short one
description: A one liner.
steps:
  - action: log
    message: hi
""", name="short.yaml")
    write_script(tmp_path, "name: broken\nsteps:\n  - action: nope\n",
                 name="broken.yaml")
    assert list_scripts(str(tmp_path)) == 0
    output = capsys.readouterr().out
    assert "stop_me\tStop me\t3 steps" in output
    assert "short\tShort one\t1 steps" in output
    assert "A one liner." in output
    # A broken script is skipped by name instead of aborting the listing.
    assert "skip broken.yaml" in output


def test_continue_policy_runs_every_step_and_counts_the_failure(tmp_path, ctx,
                                                               capsys):
    write_script(tmp_path, THREE_SENDS)
    ctx.script = Script(["ok", "CHAT_WRITE_FORBIDDEN", "ok"])
    dsl = scriptdsl.load(tmp_path / "demo.yaml")
    code = execute(config_for(tmp_path), ctx, dsl)
    output = capsys.readouterr().out
    assert code == 1
    assert output.count("step=send_text") == 3
    assert "[002/003] step=send_text account=acc1 " \
        "target=@demo content=second" in output
    assert "-> failed" in output
    assert "stop:" not in output
    assert "ok: 2" in output and "failed: 1" in output
    assert "PLAN: 3 steps, script stop_me" in output


def test_stop_policy_breaks_at_the_failed_step(tmp_path, ctx, capsys):
    body = THREE_SENDS.replace("    on_error: continue\n", "")
    write_script(tmp_path, body)
    ctx.script = Script(["ok", "CHAT_WRITE_FORBIDDEN", "ok"])
    dsl = scriptdsl.load(tmp_path / "demo.yaml")
    assert execute(config_for(tmp_path), ctx, dsl) == 1
    output = capsys.readouterr().out
    assert "[001/003]" in output and "[002/003]" in output
    assert "[003/003]" not in output
    assert "stop: stopped at step 2 (send_text)" in output
    assert "ok: 1" in output and "failed: 1" in output


def test_the_override_wins_over_the_step_policy(tmp_path, ctx, capsys):
    write_script(tmp_path, THREE_SENDS)
    ctx.script = Script(["ok", "CHAT_WRITE_FORBIDDEN", "ok"])
    dsl = scriptdsl.load(tmp_path / "demo.yaml")
    execute(config_for(tmp_path, on_error="stop"), ctx, dsl)
    output = capsys.readouterr().out
    assert "[003/003]" not in output
    assert "stop: stopped at step 2 (send_text)" in output


def test_variables_reach_the_message_and_the_counter(tmp_path, ctx, capsys):
    write_script(tmp_path, """\
name: greet
title: Greet
steps:
  - action: send_text
    text: "{{greeting}} all"
  - action: log
    message: "done, ok={{ok}}"
""", name="greet.yaml")
    ctx.script = Script(["ok"])
    dsl = DslScript(name="greet", title="Greet", steps=scriptdsl.load(
        tmp_path / "greet.yaml").steps)
    execute(config_for(tmp_path, script_name="greet",
                       variables={"greeting": "hello"}), ctx, dsl)
    output = capsys.readouterr().out
    assert "content=hello all" in output
    assert "done, ok=1" in output


def test_run_needs_a_script_name(ctx, capsys):
    assert auto_script.run(config_for(Path("/tmp"), script_name=""), ctx) == 2
    assert "no script given" in capsys.readouterr().out


def test_a_step_without_a_target_fails_cleanly(tmp_path, ctx, capsys):
    write_script(tmp_path, """\
name: no_target
steps:
  - action: send_text
    text: hi
""", name="no_target.yaml")
    dsl = scriptdsl.load(tmp_path / "no_target.yaml")
    assert execute(config_for(tmp_path, target=""), ctx, dsl) == 1
    output = capsys.readouterr().out
    assert "target=- (no target given)" in output
    assert "-> failed" in output
