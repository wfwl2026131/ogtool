"""The client surface the sender leans on, checked against Telethon itself.

Six of the names ``senders.py`` used to call -- ``join``, ``leave``,
``invite``, ``add_contact``, ``edit_profile``, ``get_contacts`` -- do not exist
on ``TelegramClient`` 1.45, so those live paths answered ``UNKNOWN`` and nothing
said so: the stand-in transport the feature tests use accepts any name at all.

``known_missing`` below is the honest inventory of that gap, and the test is
checked in both directions: a new made up name fails it, and a name that stops
being called (because it was fixed) fails it too, so the list cannot rot.
"""

from __future__ import annotations

import ast
import inspect
from unittest.mock import MagicMock

from telethon.sync import TelegramClient
from telethon.tl.functions.messages import (
    CheckChatInviteRequest,
    ImportChatInviteRequest,
)
from telethon.tl.types import Channel, ChatInvite, ChatInviteAlready

from ogtool import senders
from ogtool.features.filter_group import GroupConfig, run
from ogtool.senders import JOIN_REQUEST, TARGET_INVALID, TelethonSender
from ogtool.targets import Target, parse_many

LAB_ID = 4476576187
LAB_TITLE = "ogtool-lab"
LAB_HASH = "_kgdz_EXBVQyYTQ1"
LAB_LINK = f"https://t.me/+{LAB_HASH}"

#: What ``senders.py`` calls that the real client does not have.  Every entry is
#: one live path that answers ``UNKNOWN`` instead of doing anything; fixing one
#: means deleting its line here in the same change.
KNOWN_MISSING = {
    "add_contact": "add_contact page",
    "edit_profile": "batch_rename / auto_script profile",
    "get_contacts": "batch_invite_v2 / mass_send_v2 contact pool",
    "invite": "batch_invite / batch_invite_v2 / mix_market",
    "join": "batch_join / video_manage / auto_script",
    "leave": "video_manage / auto_script",
}


def client_calls() -> set[str]:
    """Every ``client.<name>`` in ``senders.py``, read off the source.

    Reading the file instead of running it is the point: the check has to hold
    for paths no test drives, because that is exactly where the six names hid.
    """
    tree = ast.parse(inspect.getsource(senders))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "client"
    }


def test_the_scan_finds_the_calls_it_is_meant_to_check():
    """A scan that matches nothing would pass forever without proving a thing."""
    called = client_calls()
    assert {"send_message", "get_entity"} <= called
    assert called, "the scan found no client calls"


def test_every_method_the_sender_calls_exists_on_the_real_client():
    called = client_calls()
    missing = {name for name in called if not hasattr(TelegramClient, name)}
    invented = sorted(missing - set(KNOWN_MISSING))
    fixed = sorted(set(KNOWN_MISSING) - missing)
    assert not invented, (
        f"{invented} are called on the client but do not exist on Telethon "
        f"1.45, and they are not in KNOWN_MISSING: that path is hollow")
    assert not fixed, (
        f"{fixed} no longer need a KNOWN_MISSING entry: delete their lines")


def spec_client_of() -> MagicMock:
    """A spec'd client with the two async methods a connect needs made sync.

    ``MagicMock(spec=TelegramClient)`` turns the async methods into
    ``AsyncMock``, and ``connect()`` would then hand back a coroutine -- which
    the sender rightly refuses as ``CLIENT_NOT_AWAITED``.
    """
    client = MagicMock(spec=TelegramClient)
    client.connect = MagicMock(return_value=True)
    client.disconnect = MagicMock(return_value=True)
    return client


def link_client(answer: object) -> MagicMock:
    """A spec'd client that answers every ``client(request)`` with ``answer``."""
    client = spec_client_of()
    client.side_effect = lambda request: answer
    return client


def requests_of(client: MagicMock, kind: type) -> list[object]:
    return [call.args[0] for call in client.call_args_list
            if call.args and isinstance(call.args[0], kind)]


def sender_on(settings, monkeypatch, client: object) -> TelethonSender:
    monkeypatch.setattr("telethon.sync.TelegramClient",
                        lambda *args, **kwargs: client)
    return TelethonSender(settings=settings)


def lab_channel() -> Channel:
    return Channel(id=LAB_ID, title=LAB_TITLE, photo=None, date=None,
                   megagroup=True, participants_count=3)


def lab_invite(**kwargs: object) -> ChatInvite:
    fields: dict[str, object] = {"megagroup": True, **kwargs}
    return ChatInvite(title=LAB_TITLE, photo=None, participants_count=3,
                      color=0, **fields)  # type: ignore[arg-type]


def group(entry: str) -> Target:
    return parse_many([entry], "group")[0]


def test_the_spec_client_refuses_a_method_telethon_does_not_have(spec_client):
    assert callable(spec_client.send_message)
    assert not hasattr(spec_client, "join"), (
        "a client stand-in that answers 'join' hides a method that is not "
        "there, which is how six of them stayed invisible")


def test_resolve_of_a_link_checks_the_hash_and_never_imports_it(
        settings, session, credentials, monkeypatch):
    client = link_client(ChatInviteAlready(chat=lab_channel()))
    result = sender_on(settings, monkeypatch, client).resolve(
        "t1", group(LAB_LINK))
    assert result.ok is True
    assert result.detail == LAB_TITLE
    assert result.count == 3
    # The hash goes in without its plus, and only the read-only call is made.
    assert [item.hash for item in requests_of(client, CheckChatInviteRequest)] \
        == [LAB_HASH]
    assert requests_of(client, ImportChatInviteRequest) == []


def test_resolve_of_a_link_to_a_channel_says_channel(settings, session,
                                                     credentials, monkeypatch):
    client = link_client(lab_invite(broadcast=True, megagroup=False))
    result = sender_on(settings, monkeypatch, client).resolve(
        "t1", group(LAB_LINK))
    assert result.ok is True
    assert result.code == "CHANNEL"


def test_resolve_of_a_link_behind_a_join_request_says_so(settings, session,
                                                         credentials,
                                                         monkeypatch):
    client = link_client(lab_invite(request_needed=True))
    result = sender_on(settings, monkeypatch, client).resolve(
        "t1", group(LAB_LINK))
    assert result.ok is True
    assert result.code == JOIN_REQUEST


def test_resolve_of_a_plain_group_link_can_send(settings, session, credentials,
                                                monkeypatch):
    client = link_client(lab_invite())
    result = sender_on(settings, monkeypatch, client).resolve(
        "t1", group(LAB_LINK))
    assert result.ok is True
    assert result.code == ""


def test_resolve_of_a_link_that_answers_nothing_is_a_failure(settings, session,
                                                             credentials,
                                                             monkeypatch):
    client = link_client(object())
    result = sender_on(settings, monkeypatch, client).resolve(
        "t1", group(LAB_LINK))
    assert result.ok is False
    assert result.code == TARGET_INVALID


def test_filter_group_sorts_a_group_named_by_link(settings, session,
                                                  credentials, monkeypatch,
                                                  ctx, live, capsys):
    client = link_client(ChatInviteAlready(chat=lab_channel()))
    live(sender_on(settings, monkeypatch, client))
    run(GroupConfig(account_labels=["acc1"], groups=[f"+{LAB_HASH}"],
                    global_interval_ms=0, account_interval_ms=0), ctx)
    output = capsys.readouterr().out
    assert f"+{LAB_HASH} -> 可发言" in output
    assert "UNKNOWN" not in output


def test_filter_group_still_sorts_a_group_named_by_id(settings, session,
                                                      credentials, monkeypatch,
                                                      ctx, live, capsys):
    client = spec_client_of()
    # ``get_entity`` is async on the real client too; the sync path needs a value.
    client.get_entity = MagicMock(return_value=lab_channel())
    live(sender_on(settings, monkeypatch, client))
    run(GroupConfig(account_labels=["acc1"], groups=[str(LAB_ID)],
                    global_interval_ms=0, account_interval_ms=0), ctx)
    output = capsys.readouterr().out
    assert f"{LAB_ID} -> 可发言" in output
