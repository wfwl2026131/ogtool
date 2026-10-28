"""#19 executor mechanics: slot mapping, cycles, stats, resume, speech.

The reference runs puppet chat v2 under ``PuppetChatExecutor`` with a task id
line, several patterns, whole-set cycles, per-cycle stats files
(``puppet_chat_%1_%2.json``), a puppet-account mapping table that remaps on the
daily limit and on dead accounts, ``Waiting %1 seconds before next message``,
and a closing speech log. Every test here pins one of those behaviors.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ogtool.features.puppet_chat import SlotMap, StatsFile
from ogtool.features.puppet_chat_v2 import (
    PuppetV2Config,
    load_patterns,
    run,
)


def config_for(pattern: str, **overrides) -> PuppetV2Config:
    values = {"targets": ["@own"], "pattern": pattern}
    values.update(overrides)
    return PuppetV2Config(**values)


class _SpeechSender:
    """Enough transport for the live listen window: one client per bound
    account plus a send that always succeeds, so the playback before the
    window is silent."""

    def __init__(self, *clients: object) -> None:
        self._clients = list(clients)
        self._used = 0

    def _client(self, _label: str) -> object:
        client = self._clients[self._used % len(self._clients)]
        self._used += 1
        return client

    def send(self, _account: str, _target: object, _content: object):
        from ogtool.senders import SendResult

        return SendResult(ok=True, message_id=1)

    def close(self) -> None:
        pass


# SlotMap ---------------------------------------------------------------


def test_setup_announces_the_mapping_once(capsys):
    slot_map = SlotMap(["acc1", "acc2"])
    slot_map.setup({1: "acc1", 2: "acc2"})
    out = capsys.readouterr().out
    assert "Account acc1: 1 puppets [A1]" in out
    assert "Account acc2: 1 puppets [A2]" in out
    assert "Account mapping completed: 2 puppet slots -> 2 real accounts" \
        in out
    # A second setup must not repeat the announcement.
    slot_map.setup({3: "acc1"})
    assert capsys.readouterr().out.count("Account mapping completed") == 0


def test_daily_limit_remaps_and_announces(capsys):
    slot_map = SlotMap(["acc1", "acc2"], daily_limit=1)
    slot_map.setup({4: "acc1"})
    first = slot_map.account(4)
    slot_map.note_result(first, True)
    second = slot_map.account(4)
    out = capsys.readouterr().out
    assert "Daily speech limit reached" in out
    assert "remap puppet A4" in out
    assert "Reassigned puppet A4 -> acc2" in out
    assert {first, second} == {"acc1", "acc2"}


def test_exhausted_pool_returns_none_for_a_retry(capsys):
    slot_map = SlotMap(["acc1"], daily_limit=1)
    slot_map.setup({1: "acc1"})
    account = slot_map.account(1)
    slot_map.note_result(account, True)
    assert slot_map.account(1) is None
    assert "All available accounts have reached the daily speech limit (1)" \
        in capsys.readouterr().out


def test_a_dead_account_moves_all_of_its_puppets(capsys):
    slot_map = SlotMap(["acc1", "acc2", "acc3"])
    slot_map.setup({1: "acc1", 2: "acc1"})
    slot_map.note_result("acc1", False, "SESSION_NOT_FOUND")
    out = capsys.readouterr().out
    assert "mark account unavailable account=acc1 reason=SESSION_NOT_FOUND" \
        in out
    assert "affectedPuppets=2" in out
    assert slot_map.account(1) in ("acc2", "acc3")
    assert slot_map.account(2) in ("acc2", "acc3")
    # The dead account never comes back.
    assert slot_map._usable("acc1") is False  # noqa: SLF001


def test_a_normal_failure_does_not_kill_the_account():
    slot_map = SlotMap(["acc1", "acc2"])
    slot_map.setup({1: "acc1"})
    slot_map.note_result("acc1", False, "CHAT_WRITE_FORBIDDEN")
    assert slot_map.account(1) == "acc1"
    assert "acc1" not in slot_map.dead


# StatsFile --------------------------------------------------------------


def test_stats_file_writes_the_reference_name(tmp_path: Path):
    entry = StatsFile(task="puppet-chat-v2", cycle=2, ok=3, failed=1,
                      skipped=0)
    path = entry.dump(tmp_path)
    assert path.name == "puppet_chat_puppet-chat-v2_2.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"task": "puppet-chat-v2", "cycle": 2, "ok": 3,
                       "failed": 1, "skipped": 0,
                       "written_at": pytest.approx(payload["written_at"])}


# load_patterns ----------------------------------------------------------


def test_load_patterns_deduplicates(tmp_path: Path, settings):
    one = tmp_path / "a.txt"
    one.write_text("A1|L0|D0|one\n", encoding="utf-8")
    two = tmp_path / "b.txt"
    two.write_text("A2|L0|D0|two\n", encoding="utf-8")
    config = PuppetV2Config(targets=["@own"], pattern=str(one),
                            patterns=[str(one), str(two)])
    loaded = load_patterns(config, Path(settings.data_dir))
    assert [path.name for path, _messages in loaded] == ["a.txt", "b.txt"]


# the run -----------------------------------------------------------------


def test_the_executor_line_names_task_groups_cycles_patterns(ctx,
                                                             pattern_file,
                                                             capsys):
    config = config_for(pattern_file())
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "PuppetChatExecutor started - Task ID: " in out
    assert "1 groups, 1 cycles, 1 patterns" in out


def test_cycles_replay_the_whole_set_and_write_stats(ctx, pattern_file,
                                                     capsys, settings):
    config = config_for(pattern_file(), cycles=2, cycle_interval=1,
                        stats_dir=str(Path(settings.data_dir) / "stats"))
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "CYCLE 1/2" in out
    assert "CYCLE 2/2" in out
    assert "PLAN: 8 messages" in out
    stats = list((Path(settings.data_dir) / "stats").glob(
        "puppet_chat_puppet-chat-v2_*.json"))
    assert len(stats) == 2
    by_cycle = {json.loads(item.read_text(encoding="utf-8"))["cycle"]: item
                for item in stats}
    assert json.loads(
        by_cycle[2].read_text(encoding="utf-8"))["ok"] == 4


def test_message_wait_prints_the_reference_line(ctx, tmp_path, capsys):
    flat = tmp_path / "flat.txt"
    flat.write_text("\n".join(f"A{slot}|L0|D0|line {index}"
                              for index, slot in
                              enumerate((4, 4, 5, 5), 1)) + "\n",
                    encoding="utf-8")
    run(config_for(str(flat), message_wait=7), ctx)
    out = capsys.readouterr().out
    assert "Waiting 7 seconds before next message" in out
    assert out.count("Waiting 7 seconds") == 4


def test_resume_skips_pairs_the_receipts_hold(ctx, store, pattern_file,
                                              capsys):
    from ogtool.features._common import open_task
    from ogtool.features.puppet_chat_v2 import FEATURE

    task = open_task(ctx, "prior-run", FEATURE, {}, 4)
    for account in ("acc1", "acc2"):
        store.add_result(task.id, account, "@own", "sent")

    config = config_for(pattern_file(), resume=True)
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "restore:" in out and "already-sent pair(s)" in out
    assert out.count("RESUME: skipping") == 4
    assert "ok: 0" in out
    assert "skipped: 4" in out


def test_resume_only_skips_the_sent_pairs(ctx, store, pattern_file, capsys):
    from ogtool.features._common import open_task
    from ogtool.features.puppet_chat_v2 import FEATURE

    # Only the acc1 side of the pool was ever delivered: acc2's lines replay.
    task = open_task(ctx, "prior-run", FEATURE, {}, 2)
    store.add_result(task.id, "acc1", "@own", "sent")

    config = config_for(pattern_file(), resume=True)
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert out.count("RESUME: skipping") == 2
    assert "ok: 2" in out


def test_listen_is_off_by_default_and_run_stays_clean(ctx, pattern_file,
                                                      capsys):
    assert run(config_for(pattern_file()), ctx) == 0
    out = capsys.readouterr().out
    assert "listen: off" in out
    assert "speech:" not in out


def test_the_listen_window_logs_and_filters_internal_messages(
        ctx, monkeypatch, pattern_file, capsys, slept):
    """The speech window logs arrivals and drops the puppets' own sends."""
    import asyncio
    import sys
    import types

    fake_events = types.ModuleType("telethon.events")

    class _NewMessage:
        def __init__(self, chats=None, incoming=False) -> None:
            self.chats = chats
            self.incoming = incoming

    fake_events.NewMessage = _NewMessage

    def _register(builder):
        def deco(func):
            return func

        return deco

    fake_events.register = _register
    fake_telethon = types.ModuleType("telethon")
    fake_telethon.events = fake_events
    monkeypatch.setitem(sys.modules, "telethon", fake_telethon)
    monkeypatch.setitem(sys.modules, "telethon.events", fake_events)

    class FakeLoop:
        calls = 0

        def stop(self) -> None:
            pass

        def call_later(self, seconds, action):
            del seconds
            action()  # the window closes at once in the test

        def call_soon_threadsafe(self, action):
            action()

        def run_forever(self):
            FakeLoop.calls += 1

    class FakeMe:
        def __init__(self, user_id: int) -> None:
            self.id = user_id

    class FakeClient:
        added: list[object] = []
        removed: list[object] = []

        def __init__(self, user_id: int) -> None:
            self._me = FakeMe(user_id)

        def get_me(self):
            return self._me

        def add_event_handler(self, handler):
            FakeClient.added.append(handler)

        def remove_event_handler(self, handler):
            FakeClient.removed.append(handler)

    class FakeEvent:
        def __init__(self, client, chat_id, sender_id, text) -> None:
            self.client = client
            self.chat_id = chat_id
            self.raw_text = text
            self.message = type("M", (), {"sender_id": sender_id})()

    acc1_client = FakeClient(111)
    acc2_client = FakeClient(222)
    loop = FakeLoop()
    ctx.live = True
    ctx._sender = _SpeechSender(acc1_client, acc2_client)
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: loop)

    config = config_for(pattern_file(), listen=1)
    assert run(config, ctx) == 0
    assert FakeLoop.calls >= 1, "the speech window never opened"
    handler = FakeClient.added[0]

    asyncio.run(handler(FakeEvent(acc1_client, -100, 999,
                                  "hi from outside")))
    asyncio.run(handler(FakeEvent(acc1_client, -100, 111,
                                  "hi from the puppet")))
    out = capsys.readouterr().out
    assert "speech: account=acc1 peer=-100 sender=999 text=hi from outside" \
        in out
    assert "speech: dropped internal puppet message (sender 111)" in out
    assert FakeClient.removed == FakeClient.added, \
        "the handler outlived the run"
