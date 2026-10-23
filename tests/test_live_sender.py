"""The live backend: async misuse must fail loudly, never look like success."""

from __future__ import annotations

import pytest

from ogtool.config import ProxyConfig
from ogtool.senders import (
    CLIENT_NOT_AWAITED,
    Content,
    TelethonSender,
    _proxy_tuple,
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


@pytest.fixture
def session(settings):
    path = settings.session_path("t1")
    path.write_bytes(b"")
    return path


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("DSH_SECRET_TG_API_CREDENTIALS",
                       "api_id 1234567 api_hash " + "ca" * 16)


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
