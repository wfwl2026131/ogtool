"""Batch open bot links - a console tool, not a console page.

The dump describes it in full (1737308-1737322): the three accepted link forms,
the five knobs, and the four refusals kept verbatim here.
"""

from __future__ import annotations

import pytest

from ogtool.botlinks import (
    INVALID_LINK,
    MISSING_BOT,
    NO_LINKS,
    ONLY_TELEGRAM,
    BotLinkConfig,
    BotLinkError,
    parse_link,
    parse_links,
    run,
)


def test_a_start_link_carries_the_parameter():
    link = parse_link("https://t.me/my_bot?start=abc")
    assert (link.bot, link.param, link.app) == ("my_bot", "abc", "")


def test_a_startapp_link_carries_the_app_name():
    link = parse_link("https://t.me/my_bot/app?startapp=abc&mode=compact")
    assert (link.bot, link.param, link.app) == ("my_bot", "abc", "app")


def test_a_tg_resolve_link_is_accepted():
    link = parse_link("tg://resolve?domain=my_bot&start=abc")
    assert (link.bot, link.param) == ("my_bot", "abc")


def test_a_bare_at_name_is_a_bot_without_a_parameter():
    link = parse_link("@my_bot")
    assert (link.bot, link.param) == ("my_bot", "")


def test_a_non_telegram_host_is_refused_in_the_page_words():
    with pytest.raises(BotLinkError) as caught:
        parse_link("https://example.com/my_bot?start=abc")
    assert str(caught.value) == ONLY_TELEGRAM


def test_a_telegram_url_without_a_bot_is_refused():
    with pytest.raises(BotLinkError) as caught:
        parse_link("https://t.me/")
    assert str(caught.value) == MISSING_BOT


def test_a_tg_url_without_a_domain_is_refused():
    with pytest.raises(BotLinkError) as caught:
        parse_link("tg://resolve?start=abc")
    assert str(caught.value) == MISSING_BOT


def test_junk_is_an_invalid_link():
    with pytest.raises(BotLinkError) as caught:
        parse_link("this is not a link")
    assert INVALID_LINK in str(caught.value)


def test_links_parse_one_per_line():
    links = parse_links(["https://t.me/a_bot?start=1\nhttps://t.me/b_bot\n"])
    assert [link.bot for link in links] == ["a_bot", "b_bot"]


def test_no_links_is_the_page_s_own_sentence():
    with pytest.raises(BotLinkError) as caught:
        BotLinkConfig.from_dict({}, None)
    assert str(caught.value) == NO_LINKS


def test_the_knobs_are_read_from_the_section():
    config = BotLinkConfig.from_dict({"bot_links": {
        "links": ["https://t.me/my_bot?start=abc"],
        "interval": 7,
        "pre_start": True,
        "pre_start_delay": 2,
        "concurrent_apps": 3,
        "keep_open": 30,
    }}, None)
    assert config.interval == 7
    assert config.pre_start is True
    assert config.pre_start_delay == 2
    assert config.concurrent_apps == 3
    assert config.keep_open == 30


def test_a_links_file_is_read(tmp_path):
    path = tmp_path / "links.txt"
    path.write_text("https://t.me/my_bot?start=abc\n", encoding="utf-8")
    config = BotLinkConfig.from_dict(
        {"bot_links": {"links_file": str(path)}}, None)
    assert config.links[0].bot == "my_bot"


def test_a_missing_links_file_is_reported(tmp_path):
    with pytest.raises(BotLinkError) as caught:
        BotLinkConfig.from_dict(
            {"bot_links": {"links_file": str(tmp_path / "nope.txt")}}, None)
    assert "links file not found" in str(caught.value)


def test_each_link_is_opened_once_per_account(ctx, capsys):
    config = BotLinkConfig.from_dict({"bot_links": {
        "links": ["https://t.me/my_bot?start=abc"]}}, None)
    config.interval = 0
    assert run(config, ctx) == 0
    output = capsys.readouterr().out
    assert "silently started bot my_bot" in output
    assert "Bot link task summary: success=2 total=2" in output


def test_the_pre_start_is_narrated_in_the_page_wording(ctx, capsys):
    config = BotLinkConfig.from_dict({"bot_links": {
        "links": ["https://t.me/my_bot?start=abc"], "pre_start": True,
        "pre_start_delay": 0}}, None)
    config.interval = 0
    run(config, ctx)
    output = capsys.readouterr().out
    assert "will start bot my_bot first, then trigger" in output
