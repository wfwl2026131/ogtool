"""Feature registry: one module per reference console entry."""

from __future__ import annotations

from collections.abc import Callable

from ..runtime import RunContext

Feature = Callable[[dict, RunContext], int]

_REGISTRY: dict[str, Feature] = {}


def register(name: str) -> Callable[[Feature], Feature]:
    def wrap(function: Feature) -> Feature:
        _REGISTRY[name] = function
        return function

    return wrap


def get(name: str) -> Feature:
    if name not in _REGISTRY:
        raise KeyError(f"unknown feature: {name}")
    return _REGISTRY[name]


def names() -> list[str]:
    return sorted(_REGISTRY)


def load_defaults() -> None:
    """Import the feature modules so their decorators run."""
    from . import auto_reply, keep_online, mass_send  # noqa: F401
