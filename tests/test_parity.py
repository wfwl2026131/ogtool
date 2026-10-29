"""The client has 25 console entries; the tool must have the same 25.

This file pins the mapping in one place so a page added on the client side, or
a feature renamed on this side, cannot drift apart quietly. The client list is
read from the fork when the checkout is next to this one; when it is absent the
table below is the reference, because a parity test that skips everywhere is
not a parity test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ogtool import cli
from ogtool.features import get, load_defaults, names

# (index in the console, the console label, the module that implements it)
PARITY: tuple[tuple[int, str, str], ...] = (
    (0, "Puppet Chat", "puppet_chat"),
    (1, "Copy Chat", "copy_chat"),
    (2, "AI Original Chat", "ai_chat"),
    (3, "AI Comment", "ai_comment"),
    (4, "AI Channel Repost", "ai_clone"),
    (5, "Mass Send", "mass_send"),
    (6, "Auto Reply", "auto_reply"),
    (7, "Watch Chat", "watch_chat"),
    (8, "Batch Join", "batch_join"),
    (9, "Batch Login", "batch_login"),
    (10, "Batch Rename", "batch_rename"),
    (11, "Batch Invite", "batch_invite"),
    (12, "Filter Phone", "filter_phone"),
    (13, "Filter Group", "filter_group"),
    (14, "Video Manage", "video_manage"),
    (15, "Proxy Config", "proxy"),
    (16, "Batch Contacts", "add_contact"),
    (17, "Batch Invite v2", "batch_invite_v2"),
    (18, "Mass DM v2", "mass_send_v2"),
    (19, "Puppet Chat v2", "puppet_chat_v2"),
    (20, "Auto Marketing", "mix_market"),
    (21, "Keep Online", "keep_online"),
    (22, "Advanced Features", "export_accounts"),
    (23, "Auto Script", "auto_script"),
    (24, "Settings", "settings"),
)

CLIENT_SOURCE = (Path.home()
                 / "tg-src/tdesktop/Telegram/SourceFiles/og/og_console.cpp")

#: The reference's second surface: the right toolbar (:1740461) carries
#: pages that are not console nav entries (Unread Messages / Translation /
#: Client List / QuickReply), so they cannot have a console index. ogtool
#: numbers them T1, T2, ... in the toolbar's own order.
TOOLBAR: tuple[tuple[str, str], ...] = (
    ("T1", "translate"),
)


def client_labels() -> list[str]:
    """The console labels as the fork declares them, when the fork is there."""
    if not CLIENT_SOURCE.exists():
        return [label for _index, label, _module in PARITY]
    text = CLIENT_SOURCE.read_text(encoding="utf-8")
    block = text[text.index("kNavKeys[] = {"):]
    block = block[:block.index("};")]
    return re.findall(r'"ConsoleNav::([^"]+)"', block)


def test_the_client_list_here_is_the_client_list_there():
    assert client_labels() == [label for _index, label, _module in PARITY]


def test_every_console_entry_has_a_module_in_its_own_position():
    assert len(PARITY) == 25
    assert [index for index, _label, _module in PARITY] == list(range(25))
    assert len(cli.ORDER) == len(PARITY)
    assert [module for _index, _label, module in PARITY] == cli.ORDER


def test_no_module_exists_that_the_console_does_not_show():
    load_defaults()
    console = [module for _index, _label, module in PARITY]
    toolbar = [module for _tag, module in TOOLBAR]
    assert names() == sorted(console + toolbar)


def test_every_toolbar_entry_is_registered_under_its_tag():
    load_defaults()
    for tag, module_name in TOOLBAR:
        assert callable(get(module_name)), module_name
        help_text = cli.HELP.get(module_name, "")
        assert help_text.startswith(f"#{tag} "), (module_name, help_text)


@pytest.mark.parametrize(("tag", "module_name"), TOOLBAR)
def test_the_toolbar_module_knows_its_position(tag: str, module_name: str):
    """A toolbar module without its tag is a page the toolbar outgrew."""
    from ogtool.features import module as loaded

    load_defaults()
    source = Path(loaded(module_name).__file__).read_text(encoding="utf-8")
    assert f"#{tag} " in source, (
        f"{module_name} does not carry #{tag} in its docstring")


def test_every_module_is_registered_under_its_console_number():
    load_defaults()
    for index, _label, module in PARITY:
        assert callable(get(module)), module
        help_text = cli.HELP.get(module, "")
        assert help_text.startswith(f"#{index} "), (module, help_text)


@pytest.mark.parametrize(("index", "label", "module"), PARITY)
def test_the_module_knows_its_position(index: int, label: str, module: str):
    """A module that renumbers itself is a module the console outgrew."""
    from ogtool.features import module as loaded

    load_defaults()
    source = Path(loaded(module).__file__).read_text(encoding="utf-8")
    assert f"#{index} " in source or module == "settings", (
        f"{module} does not carry #{index} ({label}) in its docstring")
