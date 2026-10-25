

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
