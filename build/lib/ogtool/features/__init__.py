"""Feature registry: one module per reference console entry."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from types import ModuleType

from ..runtime import RunContext

Feature = Callable[[dict, RunContext], int]

#: Pages that edit local configuration instead of working a queue, so there is
#: no task to close. They write a fallback receipt through
#: ``receipts.write_run`` (one row: this run, and how it ended), and this list
#: is what a test holds them to - a page may not quietly stop leaving evidence.
#: Pages that write into Telegram when run with ``--live``: they send messages,
#: invite, join, rename, comment, or relay. The CLI refuses these without
#: ``--allow-write``, because "I typed --live" and "I meant to message a hundred
#: strangers" are not the same sentence - and the sweep, the smoke suite and a
#: half-remembered script are all one keystroke apart.
WRITE_FEATURES: frozenset[str] = frozenset({
    "add_contact",
    "ai_chat",
    "ai_clone",
    "ai_comment",
    "auto_reply",
    "auto_script",
    "batch_invite",
    "batch_invite_v2",
    "batch_join",
    "batch_rename",
    "copy_chat",
    "mass_send",
    "mass_send_v2",
    "mix_market",
    "puppet_chat",
    "puppet_chat_v2",
    "video_manage",
    "watch_chat",
})

FALLBACK_PAGES: dict[str, str] = {}

_REGISTRY: dict[str, Feature] = {}
_MODULES: dict[str, ModuleType] = {}

# P1 and P0 both live here; a module is imported once, by load_defaults().
_MODULE_NAMES = [
    # P0
    "auto_reply",
    "keep_online",
    "mass_send",
    # P1
    "add_contact",
    "batch_invite",
    "batch_invite_v2",
    "batch_join",
    "batch_login",
    "batch_rename",
    "filter_group",
    "filter_phone",
    "proxy",
    "video_manage",
    "watch_chat",
    # P2
    "ai_chat",
    "ai_clone",
    "ai_comment",
    "copy_chat",
    "puppet_chat",
    "puppet_chat_v2",
    # P3
    "auto_script",
    "export_accounts",
    "mass_send_v2",
    "mix_market",
    # P3
    "settings",
]


def register(name: str, module: str = "") -> Callable[[Feature], Feature]:
    def wrap(function: Feature) -> Feature:
        _REGISTRY[name] = function
        if module:
            _MODULES[name] = importlib.import_module(f".{module}", __package__)
        return function

    return wrap


def get(name: str) -> Feature:
    if name not in _REGISTRY:
        raise KeyError(f"unknown feature: {name}")
    return _REGISTRY[name]


def module(name: str) -> ModuleType:
    if name not in _MODULES:
        raise KeyError(f"unknown feature: {name}")
    return _MODULES[name]


def names() -> list[str]:
    return sorted(_REGISTRY)


def load_defaults() -> None:
    """Import the feature modules so their decorators run."""
    for name in _MODULE_NAMES:
        loaded = importlib.import_module(f".{name}", __package__)
        _MODULES.setdefault(name, loaded)
