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
configuration, not behaviour.

The captcha automator is the same grabber class (:1741191-1741265): a bot
answer that carries a URL is a captcha link, and ``captchaMode`` picks the
handling - ``remote`` posts ``{"captchaLink", "startParam"}`` to the
panel's ``/api/_know`` and reads a ``{code, message}`` verdict
(:1741203-1741209), ``roobot`` fetches the page, parses the reference's
own hidden-input and form-action regexes (:1741210, :1741233), checks
the Timnsnems header fields (:1741250-1741258), caches the page as a
``.txt`` and submits it multipart with ``bot`` / ``msgId`` / ``redId``
parts (:1741218-1741224). The Timnsnems signature itself is computed on
the reference's compiled side - no string names the algorithm - so this
module sends the six fields as headers with an empty signature; against
the reference's own panel that fails honestly instead of forging. The
flow ends at the reference's own last captcha sentence (``Submitted to
verify.``, :1741227); what the bot does afterwards is the next grab
round.
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
#: Amounts must start with a digit: the reference feeds these regexes only
#: the bot's verdict lines, ogtool feeds them the whole reply, and a URL
#: like ``t.me`` would otherwise parse a bare ``.`` as an amount.
DALI_PATTERN = re.compile(r"\s*(USDT|CNY|TRX|JIBA)\s*:\s*(-?\d[\d\.]*)")
CNY_PATTERN = re.compile(r"\s*(\d[\d\.]*)\s*CNY")
ANY_CURRENCY_PATTERN = re.compile(r"\s*(\d[\d\.]*)\s*([A-Za-z]+)")

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
    #: Base URL the captcha API hangs off (``/api/_know``). The reference
    #: hardcodes its dev box inside the binary; here it is configuration.
    panel_base: str = ""

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> GrabConfig:
        data = section(raw, SECTION)
        panel = dict(settings.raw.get("redpacket") or {})
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
            panel=panel,
            panel_base=text(panel, "panel_base", ""),
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


# --- the captcha automator, inside the grabber class (:1741191-1741265) ---

#: A bot answer that carries a start link is a captcha link (:1741176's
#: sibling grammar :1741201: ``https?://t\.me/[^\s?]+\?start=[^\s]+``).
CAPTCHA_LINK_PATTERN = re.compile(r"https?://t\.me/[^\s?]+\?start=[^\s]+")

#: The reference's own page-scraping grammars: hidden inputs (:1741210) and
#: the form's action (:1741233).
HIDDEN_INPUT_PATTERN = re.compile(
    r'<input\b(?=[^>]*\btype="hidden")(?=[^>]*\bname="([^"]+)")'
    r'(?=[^>]*\bvalue="([^"]*)")[^>]*>'
)
FORM_ACTION_PATTERN = re.compile(r'<form[^>]*\baction="([^"]+)"')

#: The multipart part names of the roobot submission (:1741218-1741224):
#: a ``file`` part whose filename is the cached ``.txt``, plus three
#: plain parts.
ROOBOT_PARTS = ("file", "bot", "msgId", "redId")

#: The Timnsnems header fields (:1741250-1741257). The signature algorithm
#: is compiled-side only, so the fields travel and the signature is empty.
TIMNSNEMS_FIELDS = ("TGID", "Time", "Backid", "Fromid", "Singkey", "IFCODE")
TIMNSNEMS_MISSING = "RedPacket[#%1]: Missing fields for Timnsnems header."


def find_captcha_link(text: str) -> str:
    """The first captcha link in a bot answer, or ``""``."""
    match = CAPTCHA_LINK_PATTERN.search(text)
    return match.group(0) if match else ""


def parse_hidden_fields(html: str) -> dict[str, str]:
    """The page's hidden inputs, the reference's own grammar."""
    return {
        name: value
        for name, value in HIDDEN_INPUT_PATTERN.findall(html)
    }


def parse_form_action(html: str) -> str:
    """The form's action URL, the reference's own grammar."""
    match = FORM_ACTION_PATTERN.search(html)
    return match.group(1) if match else ""


def urllib_request_text(url: str, timeout: float) -> str:
    """One GET, body as text; network errors raise to the caller."""
    import urllib.request

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return answer.read().decode("utf-8", "replace")


def remote_know(
    base: str, captcha_link: str, start_param: str, timeout: float = 10.0
) -> tuple[int, str, str]:
    """One ``/api/_know`` call: status, code, message.

    The payload is the page's own pair (:1741203 ``captchaLink`` /
    ``startParam``); the verdict reads ``{code, message}`` (:1741205-1741209).
    Network errors surface as status 0 with the error text.
    """
    import json as _json
    import urllib.error
    import urllib.request

    url = base.rstrip("/") + "/api/_know"
    body = _json.dumps(
        {"captchaLink": captcha_link, "startParam": start_param}
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            status = answer.status
            payload = answer.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        status = error.code
        payload = error.read().decode("utf-8", "replace")
    except Exception as error:  # noqa: BLE001 - network noise is data here
        return 0, "", str(error)
    try:
        parsed = _json.loads(payload)
    except ValueError:
        return status, "", ""
    code = parsed.get("code") if isinstance(parsed, dict) else None
    message = parsed.get("message") if isinstance(parsed, dict) else ""
    return status, str(code or ""), str(message or "")


def missing_timnsnems(fields: dict[str, str]) -> list[str]:
    """The Timnsnems fields a page lacks - the :1741258 refusal's cause."""
    return [name for name in TIMNSNEMS_FIELDS if not fields.get(name)]


def roobot_multipart(
    html: str, bot: str, msg_id: str, red_id: str, boundary: str
) -> bytes | None:
    """The roobot multipart body, or ``None`` when the page is incomplete.

    The reference submits the page it fetched as a ``file`` part whose
    filename is a ``.txt`` (:1741218-1741224), with ``bot`` / ``msgId`` /
    ``redId`` as plain parts. A page missing any Timnsnems field builds
    nothing: the caller refuses before anything travels (:1741258).
    """
    fields = parse_hidden_fields(html)
    if missing_timnsnems(fields):
        return None
    lines = []
    for part in ROOBOT_PARTS:
        lines.append(f"--{boundary}")
        if part == "file":
            lines.append(
                'Content-Disposition: form-data; name="file"; '
                'filename="captcha.txt"'
            )
            lines.append("Content-Type: text/plain")
            lines.append("")
            lines.append(html)
        else:
            values = {"bot": bot, "msgId": msg_id, "redId": red_id}
            lines.append(f"Content-Disposition: form-data; name=\"{part}\"")
            lines.append("")
            lines.append(values[part])
    lines.append(f"--{boundary}--")
    return "\r\n".join(lines).encode("utf-8")


def roobot_submit(
    action_url: str,
    body: bytes,
    boundary: str,
    timeout: float = 15.0,
) -> tuple[int, str]:
    """One roobot multipart POST: status, body; status 0 is a network error."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        action_url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return answer.status, answer.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except Exception as error:  # noqa: BLE001 - network noise is data here
        return 0, str(error)


def _bot_message(sender: Any, account: str, bot: str, wait: float) -> tuple[str, Any]:
    """The newest bot message as ``(text, id)`` - the roobot ``msgId``."""
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
                return body, getattr(message, "id", 0)
        if time.monotonic() >= deadline:
            return "", 0
        time.sleep(0.5)


def _run_captcha(
    config: GrabConfig,
    task_id: Any,
    captcha_link: str,
    start_param: str,
    bot: str = "",
    msg_id: Any = 0,
) -> str:
    """One captcha round, the page's own sentences, mode by ``captchaMode``.

    ``remote`` posts the page's pair to ``/api/_know`` and reads the
    ``{code, message}`` verdict; ``roobot`` fetches the page, checks the
    Timnsnems fields, and submits the cached page multipart. Everything
    printed here is a reference sentence; the detail that comes back is
    what the round's receipt carries.
    """
    if config.captcha_mode == "remote":
        if not config.panel_base:
            print(
                f"RedPacket[#{task_id}]: Remote captcha request failed: "
                "no panel_base"
            )
            return "request failed: no panel_base"
        print(f"RedPacket: #{task_id} is knowing captcha")
        status, code, message = remote_know(
            config.panel_base, captcha_link, start_param,
            timeout=max(1.0, config.refresh_interval),
        )
        if status == 0:
            print(
                f"RedPacket[#{task_id}]: Remote captcha request failed: "
                f"{message}"
            )
            return f"request failed: {message}"
        if not message:
            print(f"RedPacket[#{task_id}]: Remote captcha invalid response.")
            return "invalid response"
        if code and code not in ("0",):
            print(
                f"RedPacket[#{task_id}]: Remote captcha failed, code {code} "
                f"message {message}"
            )
            return f"code {code} message {message}"
        print(f"RedPacket[#{task_id}]: Remote captcha verified successfully.")
        return f"verified {message}".strip()
    if config.captcha_mode == "roobot":
        try:
            page = urllib_request_text(captcha_link, config.refresh_interval)
        except Exception as error:  # noqa: BLE001 - the page's error line
            print(f"RedPacket[#{task_id}]: Captcha fetch error: {error}")
            return f"fetch error: {error}"
        if missing_timnsnems(parse_hidden_fields(page)):
            print(
                f"RedPacket[#{task_id}]: Missing fields for Timnsnems header."
            )
            return "missing timnsnems fields"
        action = parse_form_action(page) or (
            config.panel_base.rstrip("/") + "/api/_know"
        )
        boundary = "----ogtoolroobot"
        body = roobot_multipart(page, bot, str(msg_id), start_param, boundary)
        status, result = roobot_submit(
            action, body, boundary, timeout=max(1.0, config.refresh_interval)
        )
        if status == 0:
            print(f"RedPacket[#{task_id}]: API _know error: {result}")
            return f"api _know error: {result}"
        print(f"RedPacket: roobotcode response status {status}")
        if status != 200:
            print(f"RedPacket[#{task_id}]: API _know failed: Code {status}")
            return f"api _know code {status}"
        print(
            f"RedPacket[#{task_id}]: Submitted to verify. "
            f"Status: {status} Result {result[:80]}"
        )
        return f"verified status {status}"
    print(f"RedPacket[#{task_id}]: captcha mode {config.captcha_mode} not handled")
    return f"captcha mode {config.captcha_mode} not handled"


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
        answer, msg_id = _bot_message(sender, label, bot, config.refresh_interval)
        retry = 1
        while not answer and retry < max(1, config.rounds):
            print(
                f"RedPacket[#{task.id}]: Start response timeout, retrying "
                f"({retry}/{config.rounds})..."
            )
            answer, msg_id = _bot_message(
                sender, label, bot, config.refresh_interval
            )
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
        captcha_link = find_captcha_link(answer)
        if captcha_link and config.captcha_mode:
            print(f"RedPacket[#{task.id}]: Captcha Link: {captcha_link}")
            detail = _run_captcha(
                config, task.id, captcha_link, param, bot, msg_id
            )
            record(
                ctx, task, label, bot, False, "CAPTCHA_" +
                config.captcha_mode.upper(), detail
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
    "TIMNSNEMS_FIELDS",
    "TITLE",
    "classify_reply",
    "find_captcha_link",
    "main",
    "parse_amounts",
    "parse_form_action",
    "parse_hidden_fields",
    "parse_link",
    "remote_know",
    "roobot_submit",
    "run",
    "stats_document",
]
