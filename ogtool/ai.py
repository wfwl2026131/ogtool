"""Text generation: an OpenAI compatible endpoint, with a template fallback.

No key configured is a normal state, not an error: the features still run and
produce their plan, only the wording comes from a template instead of a model.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import AIConfig


class AIError(Exception):
    """The endpoint answered with an error or something unreadable."""


@dataclass(slots=True)
class Generated:
    text: str
    source: str      # "ai" or "template"


class AIClient:
    """One call, one completion; the caller decides what to do with the text."""

    def __init__(self, config: AIConfig, timeout: float | None = None) -> None:
        self.config = config
        self.timeout = timeout or config.timeout

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def complete(self, prompt: str, system: str = "") -> Generated:
        if not self.enabled:
            return Generated(template_text(prompt), "template")
        body = {
            "model": self.config.model,
            "messages": [
                *([{"role": "system", "content": system}] if system else []),
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.9,
        }
        request = urllib.request.Request(
            self.config.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as answer:
                payload = json.loads(answer.read().decode("utf-8"))
        except urllib.error.URLError as error:
            raise AIError(f"AI endpoint unreachable: {error}") from error
        except json.JSONDecodeError as error:
            raise AIError(f"AI endpoint returned non JSON: {error}") from error

        try:
            text = payload["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as error:
            raise AIError(f"unexpected completion shape: {payload}") from error
        if not text:
            raise AIError("AI endpoint returned an empty completion")
        return Generated(text, "ai")


def template_text(prompt: str) -> str:
    """The fallback wording: deterministic, so plans are reproducible."""
    topic = " ".join(prompt.split())[:80]
    return f"[template] {topic}" if topic else "[template]"
