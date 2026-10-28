

def test_a_locked_session_says_so():
    """Two processes on one .session: the account is busy, not revoked."""
    from ogtool.senders import code_from_exception

    code, detail, retryable = code_from_exception(
        Exception("database is locked"))
    assert code == "SESSION_BUSY"
    assert "another ogtool process" in detail
    assert retryable is True


def test_a_revoked_session_is_still_revoked():
    from ogtool.senders import code_from_exception

    code, _detail, retryable = code_from_exception(
        Exception("AUTH_KEY_UNREGISTERED"))
    assert code == "SESSION_NOT_FOUND"
    assert retryable is False


def test_a_dropped_socket_maps_to_network_error_and_is_retryable():
    """keep_online + long runs live on this: dead socket -> retryable code."""
    from ogtool.senders import code_from_exception

    code, _detail, retryable = code_from_exception(
        ConnectionError("Connection to DC closed unexpectedly"))
    assert code == "NETWORK_ERROR"
    assert retryable is True


def test_a_disconnected_cached_client_is_reconnected_on_next_use(tmp_path,
                                                                 monkeypatch,
                                                                 capsys):
    """The client cache revives a dead socket instead of failing forever."""

    from ogtool.senders import TelethonSender

    sender = TelethonSender.__new__(TelethonSender)
    sender._clients = {}
    sender._client_lock = __import__("threading").Lock()
    connects = {"n": 0}

    class Zombie:
        def is_connected(self):
            return False

        def connect(self):
            connects["n"] += 1

    sender._clients["acc1"] = Zombie()
    client = sender._client("acc1")
    assert client is sender._clients["acc1"]
    assert connects["n"] == 1
    out = capsys.readouterr().out
    assert "RECONNECT: acc1 session socket restored" in out


def test_a_cancelled_call_maps_to_network_error_and_is_retryable():
    """A disconnect racing an in-flight call is a dropped socket, not UNKNOWN."""
    import asyncio as _asyncio

    from ogtool.senders import code_from_exception

    code, _detail, retryable = code_from_exception(_asyncio.CancelledError())
    assert code == "NETWORK_ERROR"
    assert retryable is True


def test_a_connection_reset_maps_to_network_error():
    from ogtool.senders import code_from_exception

    code, _detail, retryable = code_from_exception(
        ConnectionResetError("Connection lost"))
    assert code == "NETWORK_ERROR" and retryable is True


def test_an_account_bound_proxy_wins_over_the_global_one(settings, store,
                                                         capsys):
    """The proxy page's assignment is real: the sender opens through it."""
    from dataclasses import replace

    from ogtool.config import ProxyConfig
    from ogtool.senders import TelethonSender
    from ogtool.store import Account

    store.add_account(Account("acc1", proxy="socks5://10.1.1.1:1080"))
    global_settings = replace(settings, proxy=ProxyConfig(
        kind="socks5", host="10.9.9.9", port=9999))
    sender = TelethonSender(global_settings, _store=store)

    proxy = sender._proxy_for("acc1")
    assert proxy == ("socks5", "10.1.1.1", 1080, True, None, None)
    assert "PROXY: acc1 -> socks5://10.1.1.1:1080" in capsys.readouterr().out


def test_an_unbound_account_falls_back_to_the_global_proxy(settings, store):
    from dataclasses import replace

    from ogtool.config import ProxyConfig
    from ogtool.senders import TelethonSender
    from ogtool.store import Account

    store.add_account(Account("acc2"))
    global_settings = replace(settings, proxy=ProxyConfig(
        kind="socks5", host="10.9.9.9", port=9999))
    sender = TelethonSender(global_settings, _store=store)

    proxy = sender._proxy_for("acc2")
    assert proxy == ("socks5", "10.9.9.9", 9999, True, None, None)


def test_a_broken_bound_proxy_degrades_to_the_global_one(settings, store,
                                                         capsys):
    """A typo in a bound proxy prints, does not kill the client open."""
    from dataclasses import replace

    from ogtool.config import ProxyConfig
    from ogtool.senders import TelethonSender
    from ogtool.store import Account

    store.add_account(Account("acc3", proxy="ftp://nope"))
    global_settings = replace(settings, proxy=ProxyConfig(
        kind="socks5", host="10.9.9.9", port=9999))
    sender = TelethonSender(global_settings, _store=store)

    proxy = sender._proxy_for("acc3")
    assert proxy == ("socks5", "10.9.9.9", 9999, True, None, None)
    assert "bound proxy unusable" in capsys.readouterr().out


def test_no_binding_and_no_global_means_no_proxy(settings, store):
    from ogtool.senders import TelethonSender
    from ogtool.store import Account

    store.add_account(Account("acc4"))
    sender = TelethonSender(settings, _store=store)
    assert sender._proxy_for("acc4") is None
