"""The model layer: a fixed transport, a failing one, and the audit record."""

from __future__ import annotations

from pathlib import Path

import pytest

from ogtool import templates as prompts
from ogtool.ai import AIClient, AIError
from ogtool.config import AIConfig, ConfigError


def enabled_config() -> AIConfig:
    return AIConfig(base_url="http://127.0.0.1:9", api_key="k", model="m")


def test_generate_without_a_key_falls_back_to_the_template():
    client = AIClient(AIConfig())
    result = client.generate("write something", fallback="fallback text")
    assert result.ok is False
    assert result.source == "template"
    assert result.response == "fallback text"
    assert "no AI endpoint" in result.error
    assert len(client.calls) == 1


def test_generate_uses_the_transport_and_counts_tokens():
    client = AIClient(enabled_config(),
                      transport=lambda prompt, system, config: ("hello", 12))
    result = client.generate("prompt")
    assert result.ok is True
    assert (result.response, result.tokens) == ("hello", 12)
    assert result.elapsed_ms >= 0
    assert result.label() == "ai"


def test_a_failing_transport_is_recorded_not_raised():
    def boom(prompt: str, system: str, config: AIConfig):
        raise AIError("AI endpoint timed out")

    client = AIClient(enabled_config(), transport=boom)
    result = client.generate("prompt", fallback="kept")
    assert result.ok is False
    assert result.response == "kept"
    assert "timed out" in result.error


def test_complete_raises_on_a_broken_endpoint():
    def boom(prompt: str, system: str, config: AIConfig):
        raise AIError("bad shape")

    client = AIClient(enabled_config(), transport=boom)
    with pytest.raises(AIError):
        client.complete("prompt")


def test_render_fills_placeholders_and_rejects_missing_ones():
    assert prompts.render("a {{one}} b {{ two }}", one="1", two="2") == "a 1 b 2"
    with pytest.raises(ConfigError):
        prompts.render("{{nope}}", one="1")


def test_every_shipped_template_loads_and_is_filled():
    expected = {
        "ai_chat": {"topic", "news"},
        "ai_comment": {"post", "tone", "extra", "max_chars"},
        "ai_clone": {"text", "style", "author"},
        "ai_polish": {"level", "text"},
        "translate": {"lg_from", "lg_to", "text"},
    }
    for name in prompts.NAMES:
        text = prompts.load(name)
        assert prompts.placeholders(text) == sorted(expected[name])
        values = {key: "x" for key in expected[name]}
        assert "{{" not in prompts.render(text, **values)


def test_a_missing_template_names_the_folder(tmp_path: Path):
    with pytest.raises(ConfigError) as caught:
        prompts.load("nope", folder=tmp_path)
    assert "nope.txt" in str(caught.value)
