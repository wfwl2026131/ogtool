"""Batch open bot links — the console tool the reference never lists as a page.

The dump describes it in the console's own words (line 1737308)::

    Batch open bot links with selected accounts. Supports start and startapp
    parameters.
    One bot link per line.
    Example: https://t.me/my_bot?start=abc
    Example: https://t.me/my_bot/app?startapp=abc&mode=compact
    Example: tg://resolve?domain=my_bot&start=abc

Its knobs follow immediately (1737313-1737318): Bot links, Operation interval
(seconds), Start bot before triggering link, Delay after opening bot (seconds),
Concurrent mini app count, Open keep time (seconds). And its refusals are
verbatim: "Invalid bot link", "Only Telegram bot links are supported",
"Missing bot username", "Please enter at least one bot link.", "Invalid link: %1".

This is a **tool, not a console page**: the 25-entry parity table says nothing
about it, so it lives outside the feature registry and the CLI exposes it as its
own command. That keeps the parity test honest while the behaviour is available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from .flags import Flag
from .runtime import RunContext

FEATURE = "bot_links"
SECTION = "bot_links"

#: The page's own wording for a link it will not take.
INVALID_LINK = "Invalid bot link"
ONLY_TELEGRAM = "Only Telegram bot links are supported"
MISSING_BOT = "Missing bot username"
NO_LINKS = "Please enter at least one bot link."

FLAGS = (
    Flag("--links", "links", str,
         "one bot link per line (t.me/bot?start=… or tg://resolve?…)",
         action="append"),
    Flag("--links-file", "links_file", str, "a file with one link per line"),
    Flag("--interval", "interval", float, "operation interval in seconds"),
    Flag("--pre-start", "pre_start", None,
         "start the bot first, then trigger the link", action="store_true"),
    Flag("--pre-start-delay", "pre_start_delay", float,
         "seconds between the pre-start and the trigger"),
    Flag("--concurrent-apps", "concurrent_apps", int,
         "how many mini apps may be open at once"),
    Flag("--keep-open", "keep_open", float,
         "how long a mini app stays open, in seconds"),
)


@dataclass(slots=True)
class BotLink:
    """One parsed link: the bot, the start parameter and the app name."""

    raw: str
    bot: str = ""
    param: str = ""
    app: str = ""

    def display(self) -> str:
        if self.app:
            return f"{self.bot}/app?startapp={self.param or '-'}"
        return f"{self.bot}" + (f"?start={self.param}" if self.param else "")


class BotLinkError(ValueError):
    """A link the page itself would refuse."""


def parse_link(raw: str) -> BotLink:
    """``https://t.me/bot?start=x`` / ``tg://resolve?domain=bot&start=x``.

    The refusals mirror the page: an unparsable link is "Invalid bot link", a
    non-Telegram host is "Only Telegram bot links are supported", and a Telegram
    URL without a domain is "Missing bot username".
    """
    text = (raw or "").strip()
    if not text:
        raise BotLinkError(f"{INVALID_LINK}: (empty)")
    if text.startswith("tg://"):
        parts = urlparse(text)
        query = parse_qs(parts.query)
        bot = (query.get("domain") or [""])[0].lstrip("@")
        if not bot:
            raise BotLinkError(MISSING_BOT)
        param = (query.get("start") or [""])[0]
        return BotLink(raw=text, bot=bot, param=param)
    if "://" in text:
        parts = urlparse(text)
        host = (parts.netloc or "").lower()
        if host not in ("t.me", "telegram.me", "www.t.me"):
            raise BotLinkError(ONLY_TELEGRAM)
        pieces = [piece for piece in parts.path.split("/") if piece]
        if not pieces:
            raise BotLinkError(MISSING_BOT)
        bot = pieces[0].lstrip("@")
        app = pieces[1] if len(pieces) > 1 else ""
        query = parse_qs(parts.query)
        param = (query.get("start") or query.get("startapp") or [""])[0]
        if not bot:
            raise BotLinkError(MISSING_BOT)
        return BotLink(raw=text, bot=bot, param=param, app=app)
    # A bare "@bot" or "bot" is what the page's "Missing bot username" is about.
    if text.startswith("@") and len(text) > 1:
        return BotLink(raw=text, bot=text[1:])
    raise BotLinkError(f"{INVALID_LINK}: {text}")


def parse_links(rows: list[str] | None) -> list[BotLink]:
    """Every accepted link, in order; the first bad one is reported."""
    links: list[BotLink] = []
    for row in rows or []:
        for line in str(row).splitlines():
            if not line.strip():
                continue
            links.append(parse_link(line))
    return links


@dataclass(slots=True)
class BotLinkConfig:
    account_labels: list[str] = field(default_factory=list)
    links: list[BotLink] = field(default_factory=list)
    interval: float = 5.0
    pre_start: bool = False
    pre_start_delay: float = 3.0
    concurrent_apps: int = 1
    keep_open: float = 0.0
    task_name: str = "bot-links"

    @classmethod
    def from_dict(cls, raw: dict, settings: object, live: bool = False,
                  ) -> BotLinkConfig:
        del settings
        data = raw.get(SECTION) or {}
        rows = list(data.get("links") or [])
        if not rows and not data.get("links_file"):
            raise BotLinkError(NO_LINKS)
        from pathlib import Path

        if data.get("links_file"):
            path = Path(str(data["links_file"])).expanduser()
            if not path.is_file():
                raise BotLinkError(f"links file not found: {path}")
            rows.append(path.read_text(encoding="utf-8", errors="replace"))
        try:
            links = parse_links(rows)
        except BotLinkError as error:
            raise BotLinkError(f"{error}") from None
        if not links:
            raise BotLinkError(NO_LINKS)
        return cls(
            account_labels=[str(row) for row in (raw.get("accounts") or [])],
            links=links,
            interval=max(0.0, float(data.get("interval", 5.0) or 0.0)),
            pre_start=bool(data.get("pre_start", False)),
            pre_start_delay=max(0.0, float(data.get("pre_start_delay", 3.0))),
            concurrent_apps=max(1, int(data.get("concurrent_apps", 1) or 1)),
            keep_open=max(0.0, float(data.get("keep_open", 0.0))),
            task_name=str(raw.get("task_name") or "bot-links"),
        )


def run(config: BotLinkConfig, ctx: RunContext) -> int:
    """Open each link with each account, in the page's own order and wording."""
    from .features._common import header, pause

    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts] or ["-"]
    sender = ctx.sender()

    header(config.task_name,
           links=len(config.links),
           accounts=f"{len(labels)}"
                    + (" (no account bound, plan only)" if not accounts else ""),
           interval=f"{config.interval:g}s")
    if config.pre_start:
        print(f"pre-start: on, {config.pre_start_delay:g}s before the trigger")
    print(f"concurrent mini apps: {config.concurrent_apps}"
          + (f"  keep open: {config.keep_open:g}s" if config.keep_open else ""))

    ok = failed = 0
    waited = 0.0
    for index, label in enumerate(labels, start=1):
        for link in config.links:
            if index > 1 or link is not config.links[0]:
                waited += config.interval
                pause(ctx, config.interval)
            if config.pre_start:
                delay = (f" after {config.pre_start_delay:g} seconds"
                         if config.pre_start_delay else "")
                print(f"Account {label} will start bot {link.bot} first, then "
                      f"trigger {link.display()}{delay}.")
                if config.pre_start_delay:
                    pause(ctx, config.pre_start_delay)
            result = sender.start_bot(label, link.bot, link.param)  # type: ignore[attr-defined]
            if not result.ok:
                failed += 1
                print(f"Account {label} failed to start bot {link.bot}: "
                      f"{result.code}")
                continue
            print(f"Account {label} silently started bot {link.bot}")
            if link.app or link.param:
                app = sender.request_mini_app(  # type: ignore[attr-defined]
                    label, link.bot, link.param, link.app)
                if not app.ok:
                    failed += 1
                    print(f"Account {label} failed mini app "
                          f"{link.app or link.param}: {app.code}")
                    continue
                print(f"Account {label} silently requested mini app "
                      f"{link.app or link.param}")
            ok += 1

    if ctx.live and waited:
        from .limiter import format_duration

        print(f"waited: {format_duration(waited)}")
    print(f"Bot link task summary: success={ok} total={len(labels) * len(config.links)}")
    return 0 if not failed else 1


__all__ = ["FEATURE", "FLAGS", "INVALID_LINK", "MISSING_BOT", "NO_LINKS",
           "ONLY_TELEGRAM", "SECTION", "BotLink", "BotLinkConfig",
           "BotLinkError", "parse_link", "parse_links", "run"]
