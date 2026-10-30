"""#T3 the Red Packet Grabber - start links, balances, the grab round.

The reference keeps this page in its own window (``Deep::RedPacketGrabber``,
og-strings.txt :2184543; ``startNextAccount`` / ``syncServiceInfo``): a bot
start link is handed to the pool one account at a time, the account starts
the bot with the link's parameter, and the bot's answer is parsed into a
balance or a grab verdict. This module is that round, CLI-shaped: the link
parser (:1741174-1741175), the balance grammars the reference's own
regexes define (:1741294-1741302, :1741345-1741347), the verdict lines
(:1741263-1741329), the two banners (:1741098-1741099, :1741176-1741177),
the unfinished-account line (:1741184), and the stats file the page
persists (``red_packet_stats.json``, with ``bot_counts`` / ``legacy_count``
/ ``count`` and the Dali wallet keys).

The withdrawal addresses the reference's panel protocol carries
(``addr_bsc`` / ``addr_trx``, :1737558-1737559) are read from config.yaml's
``redpacket:`` section and land in the stats file - the addresses are
configuration, not behaviour. The captcha automator (``captchaMode``,
the roobot multipart flow) is its own page-sized piece and is not here.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings
from ..flags import Flag
from ..runtime import RunContext
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    num,
    open_task,
    pause,
    record,
    section,
    text,
)

FEATURE = "redpacket"
SECTION = "redpacket"
TITLE = "#T3 红包抢包"

FLAGS = (
    Flag(
        "--link",
        "link",
        str,
        "red packet link, https://t.me/bot?start=param (grabLink)",
    ),
    Flag("--interval", "interval", float, "seconds between accounts (grabInterval)"),
    Flag(
        "--refresh-interval",
        "refresh_interval",
        float,
        "seconds to wait for each bot answer (refreshInterval)",
    ),
    Flag(
        "--rounds",
        "rounds",
        int,
        "start response retries before giving up on one account",
    ),
    Flag(
        "--per-bot-limit",
        "per_bot_limit",
        int,
        "grabs one account may make per bot (0 = unlimited)",
    ),
    Flag(
        "--captcha-mode",
        "captcha_mode",
        str,
        "the page's captchaMode, recorded and passed through",
    ),
    Flag(
        "--submit-interval",
        "submit_interval_ms",
        int,
        "the page's captchaSubmitInterval, recorded",
    ),
    Flag(
        "--stats-file",
        "stats_file",
        str,
        "where the page's red_packet_stats.json is written",
    ),
)

#: The link grammar is the reference's own (:1741174): bot username, then
#: everything after ``?start=`` is the parameter the bot is started with.
LINK_PATTERN = re.compile(r"https?://t\.me/([^/?]+)\?start=(.+)")

#: Balance grammars (:1741294-1741302). The Dali wallet form wins first -
#: four labelled currencies on one line - then the bare amount forms.
DALI_PATTERN = re.compile(r"\s*(USDT|CNY|TRX|JIBA)\s*:\s*([-\d\.]+)")
CNY_PATTERN = re.compile(r"\s*([\d\.]+)\s*CNY")
ANY_CURRENCY_PATTERN = re.compile(r"\s*([\d\.]+)\s*([A-Za-z]+)")

#: The currencies the Dali wallet tracks, in the reference's own order
#: (``dali_cny / dali_usdt / dali_trx / dali_jiba``).
DALI_KEYS = ("CNY", "USDT", "TRX", "JIBA")


class LinkError(ValueError):
    """The page's two link refusals, raised with the page's own sentence."""


@dataclass
class GrabConfig:
    """The page's fields, named after its own config keys."""

    link: str = ""
    interval: float = 3.0
    refresh_interval: float = 8.0
    rounds: int = 3
    per_bot_limit: int = 0
    captcha_mode: str = ""
    submit_interval_ms: int = 0
    stats_file: str = ""
    account_labels: list[str] = field(default_factory=list)
    #: The panel section of config.yaml: the withdrawal addresses the
    #: reference's protocol carries (``addr_bsc`` / ``addr_trx``) live here.
    panel: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> GrabConfig:
        data = section(raw, SECTION)
        return cls(
            link=text(data, "link", ""),
            interval=num(data, "interval", 3.0),
            refresh_interval=num(data, "refresh_interval", 8.0),
            rounds=int(num(data, "rounds", 3)),
            per_bot_limit=int(num(data, "per_bot_limit", 0)),
            captcha_mode=text(data, "captcha_mode", ""),
            submit_interval_ms=int(num(data, "submit_interval_ms", 0)),
            stats_file=text(data, "stats_file", ""),
            account_labels=accounts_of(raw, data),
            panel=dict(settings.raw.get("redpacket") or {}),
        )


def parse_link(link: str) -> tuple[str, str]:
    """Split the page's link into ``(bot, param)``.

    An empty link is the page's own first refusal (:1741170), a link that
    does not match the grammar is the second (:1741175).
    """
    if not link.strip():
        raise LinkError("Error: Link is empty.")
    matched = LINK_PATTERN.fullmatch(link.strip())
    if not matched:
        raise LinkError(f"RedPacket: Invalid link format: {link}")
    return matched.group(1), matched.group(2)


def parse_amounts(text: str) -> dict[str, float]:
    """Every balance the page's own grammars find in ``text``.

    Keys are upper-case currency codes; the Dali form (``CNY: 1.2``) is
    authoritative for its currencies, the bare forms (``1.2 CNY``) fill in
    what the Dali form did not name. A currency named twice keeps the first
    value, so a wallet summary is not corrupted by an echo below it. The
    bare forms scan line by line: an amount on one line and a word on the
    next are not a balance.
    """
    amounts: dict[str, float] = {}
    for code, raw_value in DALI_PATTERN.findall(text):
        key = code.upper()
        if key not in amounts:
            amounts[key] = float(raw_value)
    for line in text.splitlines():
        for raw_value, code in ANY_CURRENCY_PATTERN.findall(line):
            key = code.upper()
            if key not in amounts:
                amounts[key] = float(raw_value)
        if "CNY" in amounts:
            continue
        for raw_value, code in CNY_PATTERN.findall(line):
            if code.upper() == "CNY":
                amounts["CNY"] = float(raw_value)
                break
    return amounts


def classify_reply(text: str, amounts: dict[str, float]) -> tuple[str, str]:
    """The verdict one bot answer carries.

    ``robbed`` is an answer with an amount in it, ``limited`` is the
    service's own word, anything else is the page's unknown (:1741267).
    """
    if amounts:
        shown = ", ".join(f"{key} {amounts[key]:g}" for key in sorted(amounts))
        return "robbed", shown
    if re.search(r"limit|受限|达到上限", text, re.IGNORECASE):
        return "limited", ""
    return "unknown", " ".join(text.split())[:120]


def stats_document(
    config: GrabConfig,
    panel: dict[str, Any],
    grabs: dict[str, int],
    wallet: dict[str, float],
) -> dict:
    """The page's stats file shape (``red_packet_stats.json``)."""
    document: dict[str, Any] = {
        "count": sum(grabs.values()),
        "legacy_count": 0,
        "bot_counts": dict(sorted(grabs.items())),
    }
    for key in DALI_KEYS:
        document[f"dali_{key.lower()}"] = wallet.get(key, 0.0)
    if panel.get("addr_bsc"):
        document["addr_bsc"] = str(panel["addr_bsc"])
    if panel.get("addr_trx"):
        document["addr_trx"] = str(panel["addr_trx"])
    document["captcha_mode"] = config.captcha_mode
    return document


def _bot_reply(sender: Any, account: str, bot: str, wait: float) -> str:
    """The newest text the bot sent this account, within ``wait`` seconds.

    A bot that answers late loses: the page moves to the next account on
    its own interval, and so does this.
    """
    client = sender.client_for(account)
    deadline = time.monotonic() + max(0.5, wait)
    while True:
        try:
            messages = client.get_messages(bot, limit=3)
        except Exception:  # noqa: BLE001 - an empty answer is a valid answer
            messages = []
        for message in messages or []:
            body = getattr(message, "message", "") or ""
            if body:
                return body
        if time.monotonic() >= deadline:
            return ""
        time.sleep(0.5)


def run(config: GrabConfig, ctx: RunContext) -> int:
    try:
        bot, param = parse_link(config.link)
    except LinkError as error:
        print(str(error))
        return 2
    stats_path = (
        Path(config.stats_file).expanduser()
        if config.stats_file
        else ctx.settings.data_dir / "red_packet_stats.json"
    )
    header(
        TITLE,
        bot=bot,
        param=param[:24] + ("…" if len(param) > 24 else ""),
        accounts=len(config.account_labels),
        interval=f"{config.interval:.0f}s",
        refresh=f"{config.refresh_interval:.0f}s",
        rounds=config.rounds,
        per_bot_limit=config.per_bot_limit or "-",
        captcha_mode=config.captcha_mode or "-",
        stats=str(stats_path),
    )
    task = open_task(
        ctx,
        "redpacket",
        FEATURE,
        {"bot": bot, "param": param[:64], "live": ctx.live},
        planned=len(config.account_labels),
    )
    grabs: dict[str, int] = {}
    wallet: dict[str, float] = {}
    unfinished: list[str] = []
    if not ctx.live:
        for label in config.account_labels or ["-"]:
            print(f"PLAN {label} -> /start {param[:24]}… bot {bot}")
        close_task(ctx, task, ok=0, failed=0)
        return 0
    print(f"Red Packet Grabber started with {len(config.account_labels)} accounts.")
    print(
        f"RedPacket: Started grabbing. Bot: {bot}, Param: {param}, "
        f"Interval: {config.interval:.0f}s, "
        f"Accounts: {len(config.account_labels)}, "
        f"Captcha Mode: {config.captcha_mode or '-'}, "
        f"Submit Interval: {config.submit_interval_ms}ms"
    )
    sender = ctx.sender()
    for label in config.account_labels:
        if config.per_bot_limit and grabs.get(bot, 0) >= config.per_bot_limit:
            print(
                f"RedPacket: Account {label} reached grab limit for bot "
                f"{bot}, skipping."
            )
            continue
        try:
            client = sender.client_for(label)
            client.send_message(bot, f"/start {param}")
        except Exception as error:  # noqa: BLE001 - one account, one failure
            print(f"RedPacket: Failed to resolve bot {bot} for account {label}")
            print(f"RedPacket: {error}")
            unfinished.append(label)
            record(ctx, task, label, bot, False, "RESOLVE_FAILED", str(error))
            pause(ctx, config.interval)
            continue
        print(f"RedPacket: Account {label} started bot {bot}")
        answer = _bot_reply(sender, label, bot, config.refresh_interval)
        retry = 1
        while not answer and retry < max(1, config.rounds):
            print(
                f"RedPacket[#{task.id}]: Start response timeout, retrying "
                f"({retry}/{config.rounds})..."
            )
            answer = _bot_reply(sender, label, bot, config.refresh_interval)
            retry += 1
        if not answer:
            print(
                f"RedPacket[#{task.id}]: Start response timeout exceeded "
                f"{config.rounds} attempts, stop trying."
            )
            unfinished.append(label)
            record(
                ctx,
                task,
                label,
                bot,
                False,
                "START_TIMEOUT",
                f"no answer in {config.rounds} round(s)",
            )
            pause(ctx, config.interval)
            continue
        amounts = parse_amounts(answer)
        verdict, detail = classify_reply(answer, amounts)
        if verdict == "robbed":
            grabs[bot] = grabs.get(bot, 0) + 1
            for key, value in amounts.items():
                wallet[key] = wallet.get(key, 0.0) + value
            if "CNY" in amounts:
                print(
                    f"RedPacket[#{task.id}]: Wallet balance updated to "
                    f"{amounts['CNY']:g} CNY"
                )
            print(f"RedPacket[#{task.id}]: Robbed success. Amount: {detail}")
            record(ctx, task, label, bot, True, "ROBBED", detail)
        elif verdict == "limited":
            print(f"RedPacket: Account {label} marked as limited for bot {bot}.")
            record(ctx, task, label, bot, False, "LIMITED", "")
        else:
            print(f"RedPacket[#{task.id}]: Unknown message. {detail}")
            record(ctx, task, label, bot, False, "UNKNOWN_MSG", detail)
        pause(ctx, config.interval)
    if unfinished:
        print(
            f"RedPacket: Unfinished accounts [{', '.join(unfinished)}] "
            f"=> {len(unfinished)}"
        )
    print("Red Packet Grabber stopped.")
    document = stats_document(config, config.panel, grabs, wallet)
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Stats: {stats_path}")
    failed = len(unfinished)
    close_task(ctx, task, ok=grabs.get(bot, 0), failed=failed)
    return 0


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(GrabConfig.from_dict(raw, ctx.settings), ctx)


__all__ = [
    "FEATURE",
    "GrabConfig",
    "LinkError",
    "SECTION",
    "TITLE",
    "classify_reply",
    "main",
    "parse_amounts",
    "parse_link",
    "run",
    "stats_document",
]
