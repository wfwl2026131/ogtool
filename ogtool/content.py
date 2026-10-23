"""Message content: text, media or a forward, with AI or template wording."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ai import AIClient, AIError, template_text
from .senders import Content


@dataclass(slots=True)
class ContentSpec:
    """The content block: source, media path and message text."""

    text: str = ""
    media: str = ""
    forward_from: str = ""
    prompt: str = ""
    ai: bool = False


def build(spec: ContentSpec, ai: AIClient) -> tuple[Content, str]:
    """Turn the config into one :class:`Content` plus where the wording came from.

    ``spec.ai`` asks for generated text; a disabled or failing endpoint falls
    back to the template wording, so the plan still has something to print.
    """
    text = spec.text
    source = "given"

    if spec.ai and (spec.prompt or spec.text):
        prompt = spec.prompt or spec.text
        try:
            generated = ai.complete(prompt)
            text = generated.text or text
            source = generated.source
        except AIError:
            text = text or template_text(prompt)
            source = "template"

    if spec.media:
        media_path = Path(spec.media).expanduser()
        if not media_path.exists():
            raise FileNotFoundError(f"media file not found: {media_path}")

    return Content(text=text, media=spec.media, forward_from=spec.forward_from), source
