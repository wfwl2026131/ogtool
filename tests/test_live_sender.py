"""The live backend: async misuse must fail loudly, never look like success."""

from __future__ import annotations

import pytest

from ogtool.config import ProxyConfig
from ogtool.senders import (
    CLIENT_NOT_AWAITED,
    INVITE_HASH_EXPIRED,
    Content,
    TelethonSender,
    _code_from_class,
    _kind_of,
    _proxy_tuple,
    code_from_exception,
)
from ogtool.targets import parse_many


class AsyncishClient:
    """Stands in for ``telethon.TelegramClient``: every call returns a coroutine."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def connect(self) -> bool:
        return True

    async def send_message(self, peer: object, text: str) -> object:
        return object()

    async def is_user_authorized(self) -> bool:
        return True

    async def get_me(self) -> object:
        return object()


class SyncClient:
    """The behaviour of ``telethon.sync.TelegramClient``: real return values."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.authorized = True
        self.sent: list[tuple[object, str]] = []

    def connect(self) -> bool:
        return True

    def is_user_authorized(self) -> bool:
        return self.authorized

    def get_me(self) -> object:
        class Me:
            id = 6805350975
        return Me()

    def send_message(self, peer: object, text: str,
                     reply_to: object = None) -> object:
        self.sent.append((peer, text))

        class Message:
            id = 77
        return Message()

    def disconnect(self) -> None:
        pass


class UnauthorizedClient(SyncClient):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.authorized = False


def sender_for(settings) -> TelethonSender:
    return TelethonSender(settings=settings)


def target():
    return parse_many(["@demo_chat"], "custom")[0]


def test_a_coroutine_result_is_a_failure_not_a_success(settings, session,
                                                       credentials, monkeypatch):
    monkeypatch.setattr("telethon.sync.TelegramClient", AsyncishClient)
    result = sender_for(settings).send("t1", target(), Content(text="hi"))
    assert result.ok is False
    assert result.code == CLIENT_NOT_AWAITED
    assert "coroutine" in result.detail


def test_a_coroutine_from_ping_is_a_failure_too(settings, session, credentials,
                                                monkeypatch):
    monkeypatch.setattr("telethon.sync.TelegramClient", AsyncishClient)
    result = sender_for(settings).ping("t1")
    assert result.ok is False
    assert result.code == CLIENT_NOT_AWAITED


def test_ping_uses_a_real_sync_client(settings, session, credentials,
                                      monkeypatch):
    monkeypatch.setattr("telethon.sync.TelegramClient", SyncClient)
    result = sender_for(settings).ping("t1")
    assert result.ok is True
    assert result.code == ""
    assert result.detail == "6805350975"


def test_ping_refuses_an_unauthorized_session(settings, session, credentials,
                                              monkeypatch):
    monkeypatch.setattr("telethon.sync.TelegramClient", UnauthorizedClient)
    result = sender_for(settings).ping("t1")
    assert result.ok is False
    assert result.code == "SESSION_NOT_FOUND"
    assert "not authorized" in result.detail


def test_send_reaches_the_client(settings, session, credentials, monkeypatch):
    monkeypatch.setattr("telethon.sync.TelegramClient", SyncClient)
    result = sender_for(settings).send("t1", target(), Content(text="hi"))
    assert result.ok is True
    assert result.message_id == 77


def test_a_proxy_url_becomes_a_tuple_for_set_proxy():
    assert _proxy_tuple("socks5://1.2.3.4:1080") == (
        "socks5", "1.2.3.4", 1080, True, None, None)
    assert _proxy_tuple("socks4://u:p@5.6.7.8:1081") == (
        "socks4", "5.6.7.8", 1081, True, "u", "p")
    assert _proxy_tuple("") is None
    with pytest.raises(ValueError):
        _proxy_tuple("http://1.2.3.4:8080")


def test_the_config_proxy_is_a_string_protocol_tuple():
    proxy = ProxyConfig(kind="socks5", host="127.0.0.1", port=12888)
    assert proxy.to_telethon() == (
        "socks5", "127.0.0.1", 12888, True, None, None)
    assert not isinstance(proxy.to_telethon()[0], int)


class RecordingClient(SyncClient):
    """Keeps what the sender actually handed the client."""

    instances: list[RecordingClient] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        RecordingClient.instances.append(self)
        self.peers: list[object] = []
        self.replies: list[object] = []
        self.attempts = 0
        self.logged_out = False

    def send_message(self, peer: object, text: str,
                     reply_to: object = None) -> object:
        self.attempts += 1
        self.peers.append(peer)
        self.replies.append(reply_to)
        return super().send_message(peer, text)

    def log_out(self) -> bool:
        self.logged_out = True
        return True


class IdBlindClient(RecordingClient):
    """Rejects an int peer the way Telethon rejects an unknown id."""

    def send_message(self, peer: object, text: str,
                     reply_to: object = None) -> object:
        if isinstance(peer, int):
            self.attempts += 1
            raise ValueError(
                f'Cannot find any entity corresponding to "{peer}"')
        return super().send_message(peer, text, reply_to)


class BrokenClient(RecordingClient):
    """A failure that has nothing to do with which peer form was used."""

    def send_message(self, peer: object, text: str,
                     reply_to: object = None) -> object:
        self.attempts += 1
        raise RuntimeError("the connection died")


def numeric_target():
    return parse_many(["6805350975"], "custom")[0]


def test_a_numeric_target_goes_in_as_an_id(settings, session, credentials,
                                            monkeypatch):
    monkeypatch.setattr("telethon.sync.TelegramClient", RecordingClient)
    sender = sender_for(settings)
    result = sender.send("t1", numeric_target(), Content(text="hi"))
    client = sender._clients["t1"]._client
    assert result.ok is True
    # Telethon reads any digit string as a phone number, so it has to be an int.
    assert client.peers == [6805350975]
    assert not isinstance(client.peers[0], str)


def test_an_id_that_does_not_resolve_falls_back_to_the_phone(settings, session,
                                                             credentials,
                                                             monkeypatch):
    monkeypatch.setattr("telethon.sync.TelegramClient", IdBlindClient)
    sender = sender_for(settings)
    result = sender.send("t1", numeric_target(), Content(text="hi"))
    client = sender._clients["t1"]._client
    assert result.ok is True
    assert client.peers == ["+6805350975"]


def test_a_failure_about_the_connection_is_not_retried(settings, session,
                                                       credentials,
                                                       monkeypatch):
    monkeypatch.setattr("telethon.sync.TelegramClient", BrokenClient)
    sender = sender_for(settings)
    result = sender.send("t1", numeric_target(), Content(text="hi"))
    client = sender._clients["t1"]._client
    assert result.ok is False
    assert client.attempts == 1, "a network error must not send twice"


def test_reply_to_reaches_the_client(settings, session, credentials,
                                     monkeypatch):
    monkeypatch.setattr("telethon.sync.TelegramClient", RecordingClient)
    sender = sender_for(settings)
    result = sender.send("t1", target(), Content(text="hi", reply_to=42))
    client = sender._clients["t1"]._client
    assert result.ok is True
    assert client.replies == [42]


def test_logout_really_calls_the_client(settings, session, credentials,
                                        monkeypatch):
    monkeypatch.setattr("telethon.sync.TelegramClient", RecordingClient)
    RecordingClient.instances.clear()
    sender = sender_for(settings)
    result = sender.logout("t1")
    assert result.ok is True
    assert RecordingClient.instances[-1].logged_out is True
    assert "t1" not in sender._clients, "a logged out session must be dropped"


class TrackingClient(SyncClient):
    """Counts disconnects, so a leaked connection is visible."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.disconnects = 0

    def disconnect(self) -> None:
        self.disconnects += 1


def test_close_disconnects_every_open_client(settings):
    sender = sender_for(settings)
    first, second = TrackingClient(), TrackingClient()
    sender._clients = {"t1": first, "t2": second}
    sender.close()
    assert first.disconnects == 1
    assert second.disconnects == 1
    assert sender._clients == {}
    sender.close()  # closing an empty sender must not raise


class Said:
    """One message as Telethon hands it over."""

    next_id = 0

    def __init__(self, text: str, username: str = "alice",
                 sender_id: int = 7, date: object = None) -> None:
        Said.next_id += 1
        self.id = Said.next_id
        self.message = text
        self.sender_id = sender_id
        self.date = date
        self.sender = type("Sender", (), {"username": username})()


class HistoryClient(SyncClient):
    """Yields the rows newest first, the way ``iter_messages`` does."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.rows: list[Said] = []
        self.kwargs: dict[str, object] = {}

    def iter_messages(self, peer: object, limit: object = None):
        self.kwargs = {"limit": limit}
        return list(self.rows)


def history_sender(settings, monkeypatch, rows: list[Said]) -> tuple[object, object]:
    client = HistoryClient()
    client.rows = rows
    monkeypatch.setattr("telethon.sync.TelegramClient",
                        lambda *args, **kwargs: client)
    return sender_for(settings), client


def test_history_reads_the_chat_oldest_first(settings, session, credentials,
                                             monkeypatch):
    sender, client = history_sender(
        settings, monkeypatch, [Said("second"), Said("first")])
    result = sender.history("t1", target(), minutes=0, limit=10)
    assert result.ok
    assert result.count == 2
    assert result.items == ["@demo_chat|@alice|first",
                            "@demo_chat|@alice|second"]
    assert client.kwargs == {"limit": 10}


def test_history_stops_at_the_capture_window(settings, session, credentials,
                                             monkeypatch):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    rows = [Said("fresh", date=now - timedelta(minutes=2)),
            Said("also fresh", date=now - timedelta(minutes=9)),
            Said("too old", date=now - timedelta(minutes=40)),
            Said("ancient", date=now - timedelta(minutes=300))]
    sender, _client = history_sender(settings, monkeypatch, rows)
    result = sender.history("t1", target(), minutes=15, limit=100)
    # The window ends now; the walk stops at the first message before it,
    # instead of reading everything older than that point.
    # Returned oldest first, the order the messages were said in.
    assert result.items == ["@demo_chat|@alice|also fresh",
                            "@demo_chat|@alice|fresh"]
    assert result.count == 2


def test_history_flattens_the_record_separators(settings, session, credentials,
                                                monkeypatch):
    sender, _client = history_sender(settings, monkeypatch,
                                     [Said("a | b\nc")])
    result = sender.history("t1", target(), minutes=0, limit=5)
    assert result.items == ["@demo_chat|@alice|a / b c"]


def test_history_carries_the_post_id_a_comment_has_to_reply_to(
        settings, session, credentials, monkeypatch):
    first, second = Said("one"), Said("two")
    sender, _client = history_sender(settings, monkeypatch, [second, first])
    result = sender.history("t1", target(), minutes=0, limit=5)
    assert result.posts == [(first.id, "one"), (second.id, "two")]
    assert len(result.items) == len(result.posts)


def test_history_skips_a_post_that_carries_only_media(settings, session,
                                                      credentials, monkeypatch):
    sender, _client = history_sender(settings, monkeypatch, [Said(""), Said("hi")])
    result = sender.history("t1", target(), minutes=0, limit=5)
    assert result.items == ["@demo_chat|@alice|hi"]
    assert result.count == 2          # scanned, not captured


class CannotConnect(SyncClient):
    """A client whose connection attempt fails after the socket is up."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.disconnects = 0

    def connect(self) -> bool:
        raise ConnectionError("the proxy dropped the connection")

    def disconnect(self) -> None:
        self.disconnects += 1


def test_a_failed_connect_closes_the_half_open_client(settings, session,
                                                      credentials, monkeypatch):
    client = CannotConnect()
    monkeypatch.setattr("telethon.sync.TelegramClient",
                        lambda *args, **kwargs: client)
    sender = sender_for(settings)
    with pytest.raises(ConnectionError):
        sender._client("t1")
    assert client.disconnects == 1
    assert sender._clients == {}


class InviteHashExpiredError(Exception):
    """Telethon names the class after the answer, not the message."""


def test_the_class_name_gives_the_code_when_the_text_is_a_sentence():
    # The real exception reads "The chat the user tried to join has expired and
    # is not valid anymore", which contains no code at all.
    error = InviteHashExpiredError(
        "The chat the user tried to join has expired and is not valid "
        "anymore (caused by ImportChatInviteRequest)")
    code, detail, retryable = code_from_exception(error)
    assert code == INVITE_HASH_EXPIRED
    assert "expired" in detail
    assert retryable is False


def test_a_class_name_outside_the_table_is_still_unknown():
    class SomethingNewError(Exception):
        pass

    code, detail, _retryable = code_from_exception(SomethingNewError("nope"))
    assert code == "UNKNOWN"
    assert detail.startswith("SomethingNewError: ")


def test_a_class_name_becomes_the_code_spelling():
    assert _code_from_class("InviteHashExpiredError") == "INVITE_HASH_EXPIRED"
    assert _code_from_class("ChatAdminRequiredError") == "CHAT_ADMIN_REQUIRED"
    assert _code_from_class("TimeoutError") == "TIMEOUT"


class User:
    """Telethon's ``User``: the class name is the whole answer."""


class Channel:
    def __init__(self, *, broadcast: bool = False, megagroup: bool = False,
                 join_request: bool = False, rights: object = None) -> None:
        self.broadcast = broadcast
        self.megagroup = megagroup
        self.join_request = join_request
        self.default_banned_rights = rights


def test_a_resolved_user_is_not_a_group():
    assert _kind_of(User()) == "USER"


def test_a_broadcast_channel_is_not_somewhere_to_post():
    assert _kind_of(Channel(broadcast=True)) == "CHANNEL"
    assert _kind_of(Channel(broadcast=True, megagroup=True)) == ""
    assert _kind_of(Channel(megagroup=True)) == ""


def test_a_group_that_only_takes_requests_is_reported_as_such():
    assert _kind_of(Channel(megagroup=True, join_request=True)) == "JOIN_REQUEST"


def test_a_group_where_members_may_not_post_is_muted():
    rights = type("Rights", (), {"send_messages": True})()
    assert _kind_of(Channel(megagroup=True, rights=rights)) == "CHAT_WRITE_FORBIDDEN"
