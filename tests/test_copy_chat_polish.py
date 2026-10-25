"""``aiPolishLevel``: what each level asks for, and that it is honest about it.

The reference page has ``aiPolishEnabled`` and ``aiPolishLevel``. Level 0 never
calls the endpoint; 1..3 give the model a different instruction; a failed call
or an unset endpoint leaves the text alone and says which source it came from.
"""

from __future__ import annotations

from ogtool.features.copy_chat import (
    POLISH_PROMPTS,
    CopyConfig,
    polish_level,
    polish_plan,
    polish_text,
)


def _transport(answer: str):
    def call(prompt: str, system: str, config: object):
        del system, config
        call.prompt = prompt  # type: ignore[attr-defined]
        return answer, 7

    return call


def test_the_three_levels_have_three_different_instructions():
    assert set(POLISH_PROMPTS) == {1, 2, 3}
    assert len(set(POLISH_PROMPTS.values())) == 3


def test_level_zero_does_not_call_the_endpoint(fake_ai):
    def boom(prompt, system, config):
        raise AssertionError("level 0 must not reach the endpoint")

    ai = fake_ai(boom)
    text, source = polish_text(ai, "hello", 0)
    assert (text, source) == ("hello", "none")


def test_an_unknown_level_is_left_alone(fake_ai):
    ai = fake_ai(_transport("should not be used"))
    assert polish_text(ai, "hello", 9) == ("hello", "none")


def test_level_two_rewrites_through_the_endpoint(fake_ai):
    ai = fake_ai(_transport("你好，世界"))
    text, source = polish_text(ai, "hello world", 2)
    assert (text, source) == ("你好，世界", "ai")
    assert POLISH_PROMPTS[2] in ai.calls[0].prompt


def test_an_empty_answer_keeps_the_original(fake_ai):
    ai = fake_ai(_transport("   "))
    text, source = polish_text(ai, "hello", 3)
    assert text == "hello"
    assert source == "ai"


def test_polish_level_prefers_the_pages_own_key():
    assert polish_level(CopyConfig(polish=1, ai_polish_level=3)) == 3
    assert polish_level(CopyConfig(polish=2)) == 2
    assert polish_level(CopyConfig()) == 0


def test_one_message_is_polished_once_across_targets(fake_ai):
    from ogtool.features.copy_chat import plan_targets

    calls: list[str] = []

    def counting(prompt, system, config):
        calls.append(prompt)
        return "REWRITTEN", 3

    config = CopyConfig(
        targets=["@dest1", "@dest2"],
        messages=[],
        interval_min=0,
        interval_max=0,
    )
    from ogtool.sources import SourceMessage

    message = SourceMessage(peer="@src", sender="alice", text="buy now",
                            message_id=1)
    config = CopyConfig(targets=["@dest1", "@dest2"], messages=[message],
                        interval_min=0, interval_max=0)
    plans = plan_targets(config, ["acc1"])
    assert len(plans) == 2
    changed = polish_plan(plans, fake_ai(counting), 2)
    # One capture feeds both targets, so the message is rewritten once and
    # both copies carry the same wording.
    assert changed == 1
    assert len(calls) == 1
    for plan in plans:
        assert plan.collected.kept[0].text == "REWRITTEN"
