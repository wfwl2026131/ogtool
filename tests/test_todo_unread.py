"""The Unread page's verdicts on the todo queue: blacklist, respond, forward.

The page's own block (:1744790-1744806, :1744811, :1744817): a blacklist is
also a read, an already-blocked sender stays read, the response group is
the page's management group with its invite link applied, and a forward
that cannot find its row asks for the forwarded message.
"""

from __future__ import annotations

from ogtool import cli


def cli_run(argv):
    return cli.run(argv)


def _add(store, from_id="u1", peer="p1", msg="hello", blacklist=False):
    return store.upsert_todo(
        {
            "from_id": from_id,
            "from_name": "U",
            "peer_id": peer,
            "peer_name": "P",
            "receiver_id": "0",
            "msg": msg,
            "priority": 1,
            "is_blacklist": blacklist,
        }
    )


def _argv(settings, *rest):
    return ["--config", str(settings.path), "todo", *rest]


def test_a_blacklist_is_also_a_read(settings, store, capsys):
    row = _add(store)
    code = cli_run(_argv(settings, "blacklist", str(row)))
    out = capsys.readouterr().out
    assert code == 0
    assert "Sender blacklisted and marked as read." in out
    assert store.list_todos(None, limit=10)[0]["status"] == 2  # DONE


def test_an_already_blocked_sender_stays_read(settings, store, capsys):
    row = _add(store, blacklist=True)
    code = cli_run(_argv(settings, "blacklist", str(row)))
    out = capsys.readouterr().out
    assert code == 0
    assert "Sender already blacklisted. Marked as read." in out


def test_an_empty_selection_cannot_name_a_sender(settings, capsys):
    code = cli_run(_argv(settings, "blacklist"))
    assert code == 2
    assert "Unable to blacklist: invalid sender." in capsys.readouterr().out


def test_the_removal_names_itself(settings, store, capsys):
    row = _add(store, blacklist=True)
    code = cli_run(_argv(settings, "blacklist", str(row), "--off"))
    out = capsys.readouterr().out
    assert code == 0
    assert "Blacklist removed for this sender." in out


def test_the_removal_of_a_clean_row_refuses(settings, store, capsys):
    row = _add(store)
    code = cli_run(_argv(settings, "blacklist", str(row), "--off"))
    assert code == 1
    assert "Unable to blacklist: invalid sender." in capsys.readouterr().out


def test_a_pasted_link_is_applied_and_named(settings, store, capsys):
    code = cli_run(_argv(settings, "respond-group", "--link", "@respond"))
    out = capsys.readouterr().out
    assert code == 0
    assert "Response group ready" in out
    row = store.group_owner("response_group")
    assert row is not None and row["invite_link"] == "@respond"


def test_a_missing_link_asks_for_the_paste(settings, capsys):
    code = cli_run(_argv(settings, "respond-group"))
    assert code == 2
    assert "Paste invite link or @username" in capsys.readouterr().out


def test_a_create_without_write_refuses(settings, capsys):
    code = cli_run(
        _argv(settings, "respond-group", "--create", "--account", "acc2", "--live")
    )
    assert code == 2
    assert "--allow-write" in capsys.readouterr().out


def test_a_dry_create_plans(settings, capsys):
    code = cli_run(_argv(settings, "respond-group", "--create"))
    out = capsys.readouterr().out
    assert code == 0
    assert "dry-run: would create the response group" in out


def test_a_forward_of_unknown_rows_asks_for_the_message(settings, capsys):
    code = cli_run(_argv(settings, "forward", "999", "--target", "@t"))
    assert code == 2
    assert "Unable to resolve the todo for this command." in capsys.readouterr().out


def test_a_dry_forward_plans_each_row(settings, store, capsys):
    row = _add(store, msg="please help")
    code = cli_run(_argv(settings, "forward", str(row), "--target", "@t"))
    out = capsys.readouterr().out
    assert code == 0
    assert f"dry-run: forward #{row}" in out
    assert "please help" in out
