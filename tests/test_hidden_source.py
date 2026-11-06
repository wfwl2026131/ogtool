"""Hidden-source posts: the author's display name decides who is a target.

The reference's watch page has "Exclude hidden-source targets" (:1859807)
and logs "Monitor mode: skip hidden user G/M:%1 Name:%2" (:1743467).  A
handle (post_origins) cannot answer that: hidden is a name, not a handle,
so history() also captures post_names and the capture marks the posts that
have no resolvable author at all.
"""

from __future__ import annotations

from types import SimpleNamespace

from ogtool.features.ai_comment import (
    CommentConfig,
    capture_posts,
    exclude_hidden,
    hidden_source,
    plan,
)
from ogtool.sources import SourceMessage, parse_entry


def test_parse_entry_reads_the_author_name_alongside_the_flags():
    post = parse_entry("@c1|@alice|hello|photo.jpg|flags=forwarded|name=Alice A.")
    assert post.flags == ("forwarded",)
    assert post.author_name == "Alice A."


def test_an_old_entry_without_a_name_is_not_hidden():
    """An offline line that never learned a name is unknown, not hidden."""
    post = parse_entry("@c1|@alice|hello")
    assert post.author_name == ""
    assert not hidden_source(post)


def test_a_hidden_user_name_is_hidden():
    assert hidden_source(
        SourceMessage(peer="@c1", sender="", text="hi", author_name="Hidden User")
    )
    assert hidden_source(
        SourceMessage(peer="@c1", sender="", text="hi", author_name="deleted account")
    )


def test_a_live_post_with_no_resolvable_author_is_marked_hidden():
    assert hidden_source(
        SourceMessage(peer="@c1", sender="", text="hi", flags=("hidden_source",))
    )


class _Result:
    ok = True
    code = "OK"
    count = 2
    posts = [(7, "hello"), (8, "from nowhere")]
    post_flags = {7: ("forwarded",)}
    post_origins = {7: "@alice"}
    post_names = {7: "Alice A."}


class _Sender:
    def history(self, account, target, minutes=0, limit=0):
        return _Result()


def test_the_capture_carries_the_name_and_marks_the_nameless_post():
    posts = capture_posts(_Sender(), "acc1", ["@c1"], 5)
    assert [(p.message_id, p.author_name) for p in posts] == [(7, "Alice A."), (8, "")]
    assert "hidden_source" not in posts[0].flags
    assert "hidden_source" in posts[1].flags
    assert "forwarded" in posts[0].flags


def test_the_page_skip_line_names_the_group_and_message(capsys):
    config = CommentConfig(targets=["@c1"], exclude_hidden_source=True)
    posts = [
        SourceMessage(
            peer="@c1", sender="", text="hi", message_id=7, author_name="Hidden User"
        ),
        SourceMessage(
            peer="@c1", sender="", text="ok", message_id=8, author_name="Bob B."
        ),
    ]
    kept = exclude_hidden(config, posts)
    assert [p.message_id for p in kept] == [8]
    assert (
        "Monitor mode: skip hidden user G/M:@c1/7 Name:Hidden User"
        in capsys.readouterr().out
    )


def test_the_switch_off_keeps_hidden_posts(capsys):
    posts = [
        SourceMessage(
            peer="@c1", sender="", text="hi", message_id=7, flags=("hidden_source",)
        )
    ]
    assert exclude_hidden(CommentConfig(targets=["@c1"]), posts) == posts
    assert "skip hidden user" not in capsys.readouterr().out


def test_the_plan_drops_hidden_posts_and_plans_only_the_rest():
    config = CommentConfig(
        targets=["@c1"],
        exclude_hidden_source=True,
        posts=2,
        poll_recent=2,
        per_post_min=1,
        per_post_max=1,
        seed=3,
        messages=[
            SourceMessage(
                peer="@c1",
                sender="",
                text="hi",
                message_id=1,
                author_name="Hidden User",
            ),
            SourceMessage(
                peer="@c1", sender="", text="ok", message_id=2, author_name="Bob B."
            ),
        ],
    )
    stats = plan(config, ["acc1"])
    assert {spec.post_id for spec in stats.specs} == {2}


def test_from_dict_reads_both_key_spellings():
    for key in ("exclude_hidden_source", "excludeHiddenSource"):
        config = CommentConfig.from_dict(
            {"ai_comment": {"targets": "@c1", key: True}}, None
        )
        assert config.exclude_hidden_source is True


def test_a_missing_result_does_not_break_the_capture():
    class _Empty:
        def history(self, *args, **kwargs):
            return SimpleNamespace(
                ok=False,
                code="TIMEOUT",
                posts=[],
                post_names={},
                post_flags={},
                count=0,
            )

    assert capture_posts(_Empty(), "acc1", ["@c1"], 5) == []
