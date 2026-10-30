"""A forwarded post has two people in it: the one who wrote it, and the one
who forwarded it here.

Only the first one is a candidate to talk to, so the capture has to tell them
apart.  A channel origin has no person behind it at all, and is reported as an
empty origin rather than as a name that does not exist.
"""

from __future__ import annotations


class PeerChannel:
    """Named like Telethon's own peer type: the code compares the class name."""


class _User:
    def __init__(self, username: str = "") -> None:
        self.username = username


class _Header:
    """A forward header: where it came from, and who wrote it."""

    def __init__(self, from_id=None, channel_post=None, username="") -> None:
        self.from_id = from_id
        self.channel_post = channel_post
        self.sender = _User(username)


class _Message:
    def __init__(self, fwd_from=None, sender=None, message="", mid=1) -> None:
        self.fwd_from = fwd_from
        self.sender = sender
        self.message = message
        self.id = mid
        self.media = None


class _Client:
    """A stand-in transport that hands back a fixed list of messages."""

    def __init__(self, messages: list) -> None:
        self._messages = messages

    def iter_messages(self, peer: object, limit: object = None) -> list:
        return list(self._messages)


def test_a_plain_forward_names_the_person_who_wrote_it():
    from ogtool.senders import _forward_origin

    header = _Header(from_id=object(), username="alice")
    assert _forward_origin(_Message(fwd_from=header)) == "@alice"


def test_a_forward_without_a_username_falls_back_to_the_numeric_id():
    from ogtool.senders import _forward_origin

    class _UserId:
        user_id = 12345

    header = _Header(from_id=_UserId())
    assert _forward_origin(_Message(fwd_from=header)) == "12345"


def test_a_channel_forward_has_no_person_behind_it():
    """A channel is a place, not a person: there is nobody to name."""
    from ogtool.senders import _forward_origin

    channel_post = _Header(channel_post=7, username="alice")
    assert _forward_origin(_Message(fwd_from=channel_post)) == ""

    peer_channel = _Header(from_id=PeerChannel(), username="alice")
    assert _forward_origin(_Message(fwd_from=peer_channel)) == ""


def test_a_post_that_was_not_forwarded_has_no_origin():
    from ogtool.senders import _forward_origin

    assert _forward_origin(_Message(fwd_from=None)) == ""

    header = _Header(from_id=None)
    assert _forward_origin(_Message(fwd_from=header)) == ""


def test_the_capture_records_the_origin_next_to_the_post(settings):
    """The origin rides along with the post, so the lane can look it up."""
    from ogtool.senders import TelethonSender
    from ogtool.targets import parse_many

    header = _Header(from_id=object(), username="alice")
    client = _Client(
        [_Message(fwd_from=header, sender=_User("bob"), message="hello", mid=7)]
    )
    sender = TelethonSender(settings)
    sender._clients["acc1"] = client

    result = sender.history("acc1", parse_many(["@group"])[0])

    assert result.ok
    assert result.post_origins == {7: "@alice"}
    assert result.post_flags == {7: ("forwarded",)}
    assert result.posts == [(7, "hello")]


def test_a_typed_post_records_no_origin(settings):
    """Only forwards carry an origin; a typed post must not invent one."""
    from ogtool.senders import TelethonSender
    from ogtool.targets import parse_many

    client = _Client([_Message(sender=_User("bob"), message="hello", mid=7)])
    sender = TelethonSender(settings)
    sender._clients["acc1"] = client

    result = sender.history("acc1", parse_many(["@group"])[0])

    assert result.ok
    assert result.post_origins == {}
    assert result.post_flags == {}
