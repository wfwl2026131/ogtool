"""The username rules the page states in a tooltip and nowhere else.

"Random mode uses nickname-derived usernames 30% of the time; otherwise it picks
from word dictionary (tdata/username_words_en.txt or qrc/deep/username_words.txt).
It retries with natural suffix when occupied."

The tooltip is the whole specification: the 30%, the two dictionary paths, and
the suffix retry on occupancy only.
"""

from __future__ import annotations

import pytest

from ogtool.config import ConfigError
from ogtool.features.batch_rename import (
    NICKNAME_PERCENT,
    RenameConfig,
    load_words,
    run,
    username_candidate,
    username_from_nickname,
    with_suffix,
)


def _settings():
    return type("S", (), {"runtime": type("R", (), {
        "clamp_threads": staticmethod(lambda value: value)})()})()


class _Rng:
    """A stand-in whose ``randrange(100)`` answers from a script."""

    def __init__(self, picks):
        self.picks = list(picks)
        self.calls = 0

    def randrange(self, *args):
        self.calls += 1
        if self.picks:
            return self.picks.pop(0)
        return 0

    def choice(self, seq):
        return seq[0]


def test_the_page_default_is_thirty_percent():
    assert NICKNAME_PERCENT == 30
    config = RenameConfig.from_dict(
        {"batch_rename": {"entries": ["A B"], "username_mode": "random"}},
        _settings())
    assert config.username_nickname_percent == 30
    assert config.username_suffix_retries == 3


def test_random_is_a_username_mode_and_only_for_usernames():
    config = RenameConfig.from_dict(
        {"batch_rename": {"entries": ["A B"], "username_mode": "random"}},
        _settings())
    assert config.usernames.mode == "random"
    with pytest.raises(ConfigError):
        RenameConfig.from_dict(
            {"batch_rename": {"entries": ["A B"], "nickname_mode": "random"}},
            _settings())


def test_a_nickname_handle_is_built_from_the_name():
    handle = username_from_nickname("Alice", "Smith", _Rng([100]))
    assert handle.isalnum() and handle.startswith("alicesmith")


def test_a_nickname_handle_may_carry_two_digits():
    handle = username_from_nickname("Alice", "Smith", _Rng([0, 42]))
    assert handle.endswith("42")


def test_an_empty_name_still_yields_a_handle():
    assert username_from_nickname("!!!", "", _Rng([100])) == "user"


def test_the_suffixes_are_natural_not_random_noise():
    rng = _Rng([55, 66])
    assert with_suffix("alice", 0, rng) == "alice"
    assert with_suffix("alice", 1, rng) == "alice55"
    assert with_suffix("alice", 2, rng) == "alice_66"


def test_the_dictionary_is_loaded_from_the_first_path_that_exists(tmp_path):
    words = tmp_path / "words.txt"
    words.write_text("sunrise\nharbor\n\nbad word\n", encoding="utf-8")
    loaded = load_words((str(tmp_path / "missing.txt"), str(words)))
    assert loaded == ["sunrise", "harbor"]


def test_no_dictionary_means_an_empty_list(tmp_path):
    assert load_words((str(tmp_path / "nope.txt"),)) == []


def test_thirty_percent_splits_the_two_paths(tmp_path):
    words = tmp_path / "w.txt"
    words.write_text("harbor\n", encoding="utf-8")
    config = RenameConfig(username_mode="random",
                          username_words_file=str(words),
                          username_nickname_percent=30)
    # 29 < 30 -> nickname; 30 >= 30 -> dictionary.
    from_nickname = username_candidate(config, 0, "AliceSmith", _Rng([29, 100]))
    from_words = username_candidate(config, 0, "AliceSmith", _Rng([30, 1]))
    assert from_nickname.startswith("alicesmith")
    assert from_words.startswith("harbor")


def test_a_missing_dictionary_says_so_and_stays_on_the_nickname(tmp_path,
                                                                capsys):
    config = RenameConfig(username_mode="random",
                          username_words_file=str(tmp_path / "nope.txt"),
                          username_nickname_percent=0)
    handle = username_candidate(config, 0, "AliceSmith", _Rng([99, 100]))
    assert handle.startswith("alicesmith")
    assert "word list not found" in capsys.readouterr().out


def test_an_occupied_handle_is_retried_with_a_suffix(ctx, capsys):
    codes = iter(["USERNAME_OCCUPIED", "USERNAME_OCCUPIED", "ok"])

    class Sender:
        def update_profile(self, account, first, last):
            return type("R", (), {"ok": True, "code": "", "detail": ""})()

        def update_username(self, account, username):
            code = next(codes)
            three = ("ok", code, "")
            return type("R", (), {"ok": code == "ok", "code": three[1],
                                  "detail": ""})()

    from ogtool.features.batch_rename import RenameState, _set_username

    state = RenameState(total=1)
    _set_username(RenameConfig(username_suffix_retries=3), state, Sender(),
                  "acc1", "alice", _Rng([11, 22]))
    output = capsys.readouterr().out
    assert "已被占用" in output
    assert state.done.get("用户名") == 1


def test_a_non_occupancy_failure_is_not_retried(ctx, capsys):
    calls = []

    class Sender:
        def update_username(self, account, username):
            calls.append(username)
            return type("R", (), {"ok": False, "code": "FLOOD_WAIT_30",
                                  "detail": ""})()

    from ogtool.features.batch_rename import RenameState, _set_username

    state = RenameState(total=1)
    _set_username(RenameConfig(username_suffix_retries=3), state, Sender(),
                  "acc1", "alice", _Rng([1]))
    assert calls == ["alice"]
    assert state.failed == 1


def test_all_retries_occupied_is_counted_as_one_failure(ctx, capsys):
    class Sender:
        def update_username(self, account, username):
            return type("R", (), {"ok": False, "code": "USERNAME_OCCUPIED",
                                  "detail": ""})()

    from ogtool.features.batch_rename import RenameState, _set_username

    state = RenameState(total=1)
    _set_username(RenameConfig(username_suffix_retries=2), state, Sender(),
                  "acc1", "alice", _Rng([1, 2, 3]))
    assert state.failed == 1


def test_a_random_run_needs_no_username_list(ctx, capsys):
    config = RenameConfig(names=[("Alice", "Smith")], username_mode="random",
                          username_nickname_percent=100, seed=1,
                          operation_interval=0)
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "用户名已更新: @alicesmith" in output
