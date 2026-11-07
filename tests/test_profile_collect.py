"""#T6 Profile Collect: the group's members as a dataset.

The page's flow (:1742619-1742655): the link refusals, the access check,
the collection, the three counters, and the four ending verdicts.  The
dataset is three artefacts: nicknames.txt, bios.txt and avatars/.
"""

from __future__ import annotations

from ogtool.features.profile_collect import (
    ProfileConfig,
    parse_row,
    run,
    write_datasets,
)


def _config(**overrides) -> ProfileConfig:
    values: dict = {"task_name": "t"}
    values.update(overrides)
    return ProfileConfig(**values)


def test_a_row_splits_into_three_fields():
    assert parse_row("Alice A.|hello|/x/a.jpg") == ("Alice A.", "hello", "/x/a.jpg")
    assert parse_row("Bob||") == ("Bob", "", "")
    assert parse_row("||") == ("", "", "")


def test_the_datasets_carry_the_page_counters(tmp_path):
    rows = [("Alice", "hi", "/x/a.jpg"), ("", "", ""), ("Carl", "", "")]
    counters = write_datasets(tmp_path, rows)
    assert counters == {"nicknames": 2, "bios": 1, "avatars": 1}
    assert "Alice\nCarl\n" in (tmp_path / "nicknames.txt").read_text("utf-8")
    assert (tmp_path / "avatars").is_dir()


def test_an_empty_link_is_the_page_refusal(ctx, capsys):
    assert run(_config(), ctx) == 2
    assert "No group link provided" in capsys.readouterr().out


def test_a_dry_run_plans_without_a_session(ctx, capsys):
    assert run(_config(group="@sweep"), ctx) == 0
    out = capsys.readouterr().out
    assert "PLAN" in out and "@sweep" in out
    assert "Checking group access" not in out


class _Sender:
    """A stand-in transport whose members carry display|bio|avatar rows."""

    def __init__(self, items, ok=True):
        self._items = items
        self._ok = ok

    def resolve(self, account, target):
        from types import SimpleNamespace

        return SimpleNamespace(ok=self._ok, code="" if self._ok else "X", detail="peer")

    def profiles(self, account, target, limit=0, avatars=""):
        from types import SimpleNamespace

        return SimpleNamespace(
            ok=self._ok,
            code="" if self._ok else "X",
            count=len(self._items),
            items=list(self._items),
        )

    def close(self):
        return None


def test_a_live_collection_writes_the_datasets(ctx, capsys):
    ctx.live = True
    ctx._sender = _Sender(["Alice A.|hi|", "Bob||"])
    out_dir = ctx.settings.data_dir / "profiles"
    config = _config(group="@sweep", avatars=False)
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "Checking group access" in out
    assert "Group accessible, starting collection" in out
    assert "Nickname: 2/2" in out
    assert "Collection completed successfully" in out
    assert "Saved to:" in out
    assert (out_dir / "nicknames.txt").is_file()


def test_a_thin_dataset_is_insufficient(ctx, capsys):
    ctx.live = True
    ctx._sender = _Sender(["|x|"])
    config = _config(group="@sweep", avatars=False)
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "Collection finished, insufficient data" in out


def test_an_unreachable_group_stops_the_round(ctx, capsys):
    ctx.live = True
    ctx._sender = _Sender([], ok=False)
    assert run(_config(group="@sweep"), ctx) == 1
    assert "Unable to access the group" in capsys.readouterr().out
