

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
