"""Text generation: an OpenAI compatible endpoint, with a template fallback.

No key configured is a normal state, not an error: the features still run and
produce their plan, only the wording comes from a template instead of a model.

Every call can be turned into an :class:`AIResult`, which carries what was
asked, what came back, how long it took and whether it worked -- that record is
what the pages store per task, so a run can be audited afterwards.  ``transport``
exists so the tests (and anyone without a key) can answer deterministically,
including the failing and timing out branches.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from .config import AIConfig


class AIError(Exception):
    """The endpoint answered with an error or something unreadable."""


@dataclass(slots=True)
class Generated:
    text: str
    source: str      # "ai" or "template"


@dataclass(slots=True)
class AIResult:
    """One call, in the shape a task keeps for the audit trail."""

    prompt: str
    system: str = ""
    response: str = ""
    source: str = "template"      # "ai" or "template"
    tokens: int = 0
    elapsed_ms: int = 0
    ok: bool = True
    error: str = ""

    def label(self) -> str:
        return "ai" if self.ok and self.source == "ai" else "template"


#: ``(prompt, system, config) -> (text, tokens)``; tests pass their own.
Transport = Callable[[str, str, AIConfig], tuple[str, int]]


class AIClient:
    """One call, one completion; the caller decides what to do with the text."""

    def __init__(self, config: AIConfig, timeout: float | None = None,
                 transport: Transport | None = None) -> None:
        self.config = config
        self.timeout = timeout or config.timeout
        self.transport = transport
        self.calls: list[AIResult] = []

    @property
    def enabled(self) -> bool:
        """A transport counts as an endpoint: that is what makes mocks work."""
        return self.transport is not None or self.config.enabled

    def complete(self, prompt: str, system: str = "") -> Generated:
        """One completion; raises :class:`AIError` when the endpoint fails."""
        if not self.enabled:
            return Generated(template_text(prompt), "template")
        text, _ = self._request(prompt, system)
        return Generated(text, "ai")

    def generate(self, prompt: str, system: str = "", *,
                 fallback: str = "") -> AIResult:
        """A completion that never raises: template wording when it fails.

        This is the call the pages use, because one unavailable endpoint must
        not take a whole task down with it.
        """
        started = time.perf_counter()
        result = AIResult(prompt=prompt, system=system)
        try:
            if not self.enabled:
                raise AIError("no AI endpoint configured")
            text, tokens = self._request(prompt, system)
            result.response = text
            result.source = "ai"
            result.tokens = tokens
            result.ok = True
        except AIError as error:
            result.response = fallback or template_text(prompt)
            result.source = "template"
            result.ok = False
            result.error = str(error)
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        self.calls.append(result)
        return result

    def _request(self, prompt: str, system: str) -> tuple[str, int]:
        if self.transport is not None:
            return self.transport(prompt, system, self.config)
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
        except TimeoutError as error:
            raise AIError(f"AI endpoint timed out: {error}") from error
        except json.JSONDecodeError as error:
            raise AIError(f"AI endpoint returned non JSON: {error}") from error

        try:
            text = payload["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as error:
            raise AIError(f"unexpected completion shape: {payload}") from error
        tokens = 0
        usage = payload.get("usage") or {}
        if isinstance(usage, dict):
            tokens = int(usage.get("completion_tokens")
                         or usage.get("total_tokens") or 0)
        if not text:
            raise AIError("AI endpoint returned an empty completion")
        return text, tokens


@dataclass(slots=True)
class Recorder:
    """Where :func:`record` puts the calls a task made."""

    rows: list[AIResult] = field(default_factory=list)

    def add(self, result: AIResult) -> None:
        self.rows.append(result)

    @property
    def failed(self) -> int:
        return sum(1 for row in self.rows if not row.ok)

    def summary(self) -> str:
        total = len(self.rows)
        return f"ai calls {total} (failed {self.failed})"


def template_text(prompt: str) -> str:
    """The fallback wording: deterministic, so plans are reproducible."""
    topic = " ".join(prompt.split())[:80]
    return f"[template] {topic}" if topic else "[template]"


__all__ = ["AIError", "AIResult", "AIClient", "Generated", "Recorder",
           "Transport", "template_text"]
