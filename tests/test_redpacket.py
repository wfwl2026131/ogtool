"""The Red Packet Grabber's round, key by key.

The reference's own pieces: the link grammar and its two refusals
(:1741170-1741175), the balance grammars (:1741294-1741302,
:1741345-1741347), the verdict lines (:1741263-1741329), the banners
(:1741098-1741099, :1741176-1741177), the unfinished line (:1741184),
the stats document (``red_packet_stats.json`` with ``bot_counts`` and the
Dali wallet keys), and the panel's withdrawal addresses (:1737558-1737559).
The bot is never contacted by a test: the sender seam is faked.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ogtool.features.redpacket import (
    GrabConfig,
    LinkError,
    classify_reply,
    parse_amounts,
    parse_link,
    run,
    stats_document,
)

# --- the link parser (:1741170-1741175) -------------------------------


def test_a_link_splits_into_bot_and_param():
    assert parse_link("https://t.me/DaliRedBot?start=c_12345") == (
        "DaliRedBot",
        "c_12345",
    )


def test_an_empty_link_is_the_page_s_own_refusal():
    with pytest.raises(LinkError) as error:
        parse_link("   ")
    assert "Error: Link is empty." in str(error.value)


def test_a_malformed_link_is_the_page_s_other_refusal():
    with pytest.raises(LinkError) as error:
        parse_link("t.me/DaliRedBot")
    assert "RedPacket: Invalid link format: t.me/DaliRedBot" in str(error.value)


# --- the balance grammars (:1741294-1741347) --------------------------


def test_the_dali_wallet_form_is_parsed():
    amounts = parse_amounts("钱包 CNY: 12.5 USDT: 3 TRX: 0 JIBA: 7.25")
    assert amounts == {"CNY": 12.5, "USDT": 3.0, "TRX": 0.0, "JIBA": 7.25}


def test_the_bare_amount_forms_fill_in():
    assert parse_amounts("You got 2.75 CNY") == {"CNY": 2.75}
    assert parse_amounts("balance 1.5 USDT") == {"USDT": 1.5}


def test_the_first_mention_wins_so_an_echo_below_does_not_corrupt():
    text = "CNY: 5.0\nold balance CNY: 0.0"
    assert parse_amounts(text) == {"CNY": 5.0}


def test_a_text_without_amounts_parses_empty():
    assert parse_amounts("please wait, captcha required") == {}


# --- the verdict (:1741263-1741267) -----------------------------------


def test_an_answer_with_amounts_is_robbed():
    verdict, detail = classify_reply("Robbed CNY: 8.8", {"CNY": 8.8})
    assert verdict == "robbed"
    assert "CNY 8.8" in detail


def test_a_limit_answer_is_limited():
    verdict, _ = classify_reply("You reached the grab limit", {})
    assert verdict == "limited"


def test_anything_else_is_unknown():
    verdict, detail = classify_reply("hello there", {})
    assert verdict == "unknown"
    assert "hello there" in detail


# --- the stats document (red_packet_stats.json) -----------------------


def test_the_stats_document_carries_the_page_s_own_keys():
    config = GrabConfig(captcha_mode="remote")
    panel = {"addr_bsc": "0xabc", "addr_trx": "TXYz"}
    document = stats_document(
        config, panel, {"DaliRedBot": 3}, {"CNY": 10.5, "USDT": 2.0}
    )
    assert document["count"] == 3
    assert document["legacy_count"] == 0
    assert document["bot_counts"] == {"DaliRedBot": 3}
    assert document["dali_cny"] == 10.5
    assert document["dali_usdt"] == 2.0
    assert document["dali_trx"] == 0.0
    assert document["dali_jiba"] == 0.0
    assert document["addr_bsc"] == "0xabc"
    assert document["addr_trx"] == "TXYz"
    assert document["captcha_mode"] == "remote"


def test_addresses_are_absent_when_the_panel_has_none():
    document = stats_document(GrabConfig(), {}, {}, {})
    assert "addr_bsc" not in document
    assert "addr_trx" not in document


# --- the run round -----------------------------------------------------


class FakeSender:
    """One scripted answer per account, then the count of starts."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.starts: list[tuple[str, str, str]] = []

    def client_for(self, account: str):
        bot_holder = self

        class Client:
            def send_message(self, bot: str, text: str) -> None:
                bot_holder.starts.append((account, bot, text))

            def get_messages(self, peer: str, limit: int = 3):
                body = bot_holder.answers.get(account, "")
                return [SimpleNamespace(message=body)] if body else []

        return Client()


def _live_ctx(ctx, sender, panel=None):
    return SimpleNamespace(
        settings=SimpleNamespace(
            data_dir=ctx.settings.data_dir,
            raw={"redpacket": panel or {}},
        ),
        store=ctx.store,
        log=ctx.log,
        live=True,
        sender=lambda: sender,
        waited=0.0,
    )


def _config(link: str, accounts: list[str], **overrides) -> GrabConfig:
    base = {
        "link": link,
        "interval": 0.0,
        "refresh_interval": 0.0,
        "rounds": 2,
        "account_labels": accounts,
    }
    base.update(overrides)
    return GrabConfig(**base)


def test_a_live_round_robs_and_writes_the_stats_file(ctx, store, tmp_path):
    sender = FakeSender({"acc1": "Robbed CNY: 8.8"})
    stats = tmp_path / "red_packet_stats.json"
    config = _config(
        "https://t.me/DaliRedBot?start=c_1",
        ["acc1"],
        stats_file=str(stats),
    )
    config.panel = {"addr_bsc": "0xabc"}
    assert run(config, _live_ctx(ctx, sender)) == 0
    assert sender.starts == [("acc1", "DaliRedBot", "/start c_1")]
    document = json.loads(stats.read_text(encoding="utf-8"))
    assert document["count"] == 1
    assert document["bot_counts"] == {"DaliRedBot": 1}
    assert document["dali_cny"] == 8.8
    assert document["addr_bsc"] == "0xabc"


def test_a_silent_bot_is_an_unfinished_account(ctx, store, tmp_path, capsys):
    sender = FakeSender({})
    stats = tmp_path / "s.json"
    config = _config("https://t.me/B?start=x", ["acc1"], stats_file=str(stats))
    code = run(config, _live_ctx(ctx, sender))
    out = capsys.readouterr().out
    assert code == 0
    assert "Start response timeout exceeded 2 attempts, stop trying." in out
    assert "RedPacket: Unfinished accounts [acc1] => 1" in out
    assert "Red Packet Grabber stopped." in out


def test_a_limit_answer_marks_the_account(ctx, store, tmp_path, capsys):
    sender = FakeSender({"acc1": "You reached the grab limit"})
    stats = tmp_path / "s.json"
    config = _config("https://t.me/B?start=x", ["acc1"], stats_file=str(stats))
    run(config, _live_ctx(ctx, sender))
    out = capsys.readouterr().out
    assert "RedPacket: Account acc1 marked as limited for bot B." in out


def test_the_banners_and_the_grab_line_are_the_page_s_own(ctx, store, tmp_path, capsys):
    sender = FakeSender({"acc1": "2.5 CNY"})
    stats = tmp_path / "s.json"
    config = _config(
        "https://t.me/DaliRedBot?start=p1",
        ["acc1"],
        stats_file=str(stats),
        captcha_mode="remote",
        submit_interval_ms=700,
    )
    run(config, _live_ctx(ctx, sender))
    out = capsys.readouterr().out
    assert "Red Packet Grabber started with 1 accounts." in out
    assert (
        "RedPacket: Started grabbing. Bot: DaliRedBot, Param: p1, "
        "Interval: 0s, Accounts: 1, Captcha Mode: remote, "
        "Submit Interval: 700ms"
    ) in out
    assert "Wallet balance updated to 2.5 CNY" in out
    assert "RedPacket[#" in out and "Robbed success. Amount: CNY 2.5" in out


def test_the_per_bot_limit_skips_the_second_account(ctx, store, tmp_path, capsys):
    sender = FakeSender({"acc1": "1 CNY", "acc2": "1 CNY"})
    stats = tmp_path / "s.json"
    config = _config(
        "https://t.me/B?start=x",
        ["acc1", "acc2"],
        stats_file=str(stats),
        per_bot_limit=1,
    )
    run(config, _live_ctx(ctx, sender))
    out = capsys.readouterr().out
    assert "reached grab limit for bot B, skipping." in out
    assert [item[0] for item in sender.starts] == ["acc1"]


def test_a_dry_run_plans_and_touches_nothing(ctx, store, tmp_path, capsys):
    sender = FakeSender({})
    stats = tmp_path / "never.json"
    config = _config("https://t.me/B?start=x", ["acc1"], stats_file=str(stats))
    dry = SimpleNamespace(
        settings=SimpleNamespace(data_dir=ctx.settings.data_dir, raw={"redpacket": {}}),
        store=ctx.store,
        log=ctx.log,
        live=False,
        sender=lambda: sender,
        waited=0.0,
    )
    assert run(config, dry) == 0
    out = capsys.readouterr().out
    assert "PLAN acc1 -> /start x… bot B" in out
    assert sender.starts == []
    assert not stats.exists()
