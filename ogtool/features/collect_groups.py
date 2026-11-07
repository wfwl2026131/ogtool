"""#T7 Collect Group Links - the @jisou bot scraper (YingFa service).

``Deep::AutoScriptYingFaService`` runs the loop; ``YingFaWidget`` is the
page (:2184989-2184990, ``sendBotCommand`` / ``sendRefreshBalanceCommand``
in the RTTI block).  The page's own round (:1737386-1747426): keywords
in, worker accounts out, ``/start`` to the bot, the filter confirmation
button pressed when the bot asks, the keyboard buttons read row by row
(:1747420), next pages requested (:1747421), and every link collected
into the output txt with a CSV beside it (:1747402).  The four endings
are the page's own (:1747408-1747410), and each step speaks its line:
searching (:1747413), pressing (:1747416), no response (:1747417),
throttled (:1747418), collected count (:1747419), the bot refusals
(:1747424-1747425) and the two start gates (:1747404-1747405).

The bot is the page's own (@jisou :1737386, ``collect_group_link``
:1737387, ``CollectGroups`` :1737388); a dry run plans the keywords
without a session.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ConfigError, Settings
from ..flags import Flag
from ..runtime import RunContext
from ..senders import Content
from ..targets import parse_many
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    name_of,
    num,
    open_task,
    record,
    section,
    text,
)

FEATURE = "collect_groups"
SECTION = "collect_groups"
TITLE = "#T7 采群链接"

FLAGS = (
    Flag("--keywords", "keywords", str, "keywords, comma or newline separated"),
    Flag("--keywords-file", "keywords_file", str, "file with one keyword per line"),
    Flag("--bot", "bot", str, "the bot username (default @jisou)"),
    Flag("--limit", "limit", int, "Collection limit (0 = unlimited)"),
    Flag(
        "--out",
        "out",
        str,
        "Output file (*.txt will be used and CSV generated with same name)",
    ),
)

#: The bot and its command the page hardcodes (:1737386-1737388).
JISOU_BOT = "jisou"
COLLECT_COMMAND = "collect_group_link"
COLLECT_MODE = "CollectGroups"

#: The link grammar the collected lines are kept by: a t.me link or a bare
#: @username, the two shapes the bot's answer carries.
LINK_PATTERN = re.compile(r"(?:https?://)?t\.me/[^\s,，]+|@[A-Za-z][A-Za-z0-9_]{4,31}")

#: Keyboard button text that asks for a next page (:1747421's cause).
NEXT_PAGE_WORDS = ("more", "next", "下一页", "更多")


@dataclass(slots=True)
class CollectConfig:
    account_labels: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    bot: str = JISOU_BOT
    limit: int = 0
    out: str = ""
    task_name: str = "collect-groups"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> CollectConfig:
        data = section(raw, SECTION)
        keywords = [
            part.strip()
            for part in text(data, "keywords").replace("\n", ",").split(",")
            if part.strip()
        ]
        for path in (
            data.get("keywords_file") or []
            if isinstance(data.get("keywords_file"), list)
            else [data.get("keywords_file")]
        ):
            if not path:
                continue
            file = Path(str(path)).expanduser()
            try:
                keywords.extend(
                    line.strip()
                    for line in file.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
            except OSError as error:
                raise ConfigError(f"{path}: {error}") from error
        return cls(
            account_labels=accounts_of(raw, data),
            keywords=keywords,
            bot=text(data, "bot") or JISOU_BOT,
            limit=int(num(data, "limit", 0, kind=int, minimum=0)),
            out=text(data, "out"),
            task_name=name_of(raw, data, "collect-groups"),
        )


def links_of(reply: str) -> list[str]:
    """The links one bot answer carries, in order, deduplicated."""
    found = LINK_PATTERN.findall(reply or "")
    seen: set[str] = set()
    unique: list[str] = []
    for link in found:
        if link not in seen:
            seen.add(link)
            unique.append(link)
    return unique


def next_page_button(labels: list[str]) -> str | None:
    """The button that asks for more (:1747421), or none."""
    for label in labels:
        lowered = (label or "").casefold()
        if any(word in lowered for word in NEXT_PAGE_WORDS):
            return label
    return None


def parse_buttons(reply: str) -> list[list[str]]:
    """The bot answer's keyboard rows, as the page logs them (:1747420).

    The transport reports rows as ``row|col|text`` lines; a reply without
    buttons has none.  (Live, the client's reply markup is the source.)
    """
    rows: list[list[str]] = []
    for line in (reply or "").splitlines():
        parts = line.split("|")
        if (
            len(parts) == 3
            and parts[0].strip().isdigit()
            and parts[1].strip().isdigit()
        ):
            row_index = int(parts[0].strip())
            while len(rows) <= row_index:
                rows.append([])
            rows[row_index].append(parts[2].strip())
    return rows


def write_outputs(path: Path, links: list[str], keyword: str) -> None:
    """The txt the page names and the CSV beside it (:1747402)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"# {keyword}\n")
        for link in links:
            handle.write(f"{link}\n")
    csv_path = path.with_suffix(".csv")
    new_file = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new_file:
            writer.writerow(["keyword", "link"])
        for link in links:
            writer.writerow([keyword, link])


def run(config: CollectConfig, ctx: RunContext) -> int:
    if not config.keywords:
        # The page's own start gates (:1747404-1747405).
        print("Please input at least one keyword.")
        return 2
    if ctx.live and not config.account_labels:
        # The page's Start gate (:1747404): a live run with no worker
        # selected is refused before the pool is even bound.
        print("Select at least one worker account.")
        return 2
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts]
    if not labels:
        if ctx.live:
            # The page's Start gate (:1747404) is a live refusal: a dry run
            # plans instead, the same downgrade an empty pool earns on the
            # other pages.
            print("Select at least one worker account.")
            return 2
        labels = ["-"]
    out_path = (
        Path(config.out).expanduser()
        if config.out
        else ctx.settings.data_dir / "collected_links.txt"
    )
    bot = parse_many([f"@{config.bot.lstrip('@')}"], "user")[0]
    header(
        TITLE,
        accounts=f"{len(labels)}",
        keywords=len(config.keywords),
        bot=f"@{config.bot.lstrip('@')}",
        limit=config.limit or "-",
        out=str(out_path),
    )
    task = open_task(
        ctx,
        config.task_name,
        FEATURE,
        {"keywords": len(config.keywords), "bot": config.bot, "out": out_path.name},
        planned=len(config.keywords) * len(labels),
    )
    collected: dict[str, int] = {}
    total_links = 0
    processed = 0
    stopped_reason = ""
    if ctx.live:
        print("Collection started.")
    keyword_failed = False
    for keyword in config.keywords:
        if config.limit and processed >= config.limit:
            print("Collection limit reached.")
            stopped_reason = "limit"
            break
        for label in labels:
            if config.limit and processed >= config.limit:
                print("Collection limit reached.")
                stopped_reason = "limit"
                break
            processed += 1
            if not ctx.live:
                print(f'PLAN {label} searching keyword "{keyword}"')
                continue
            print(f'Account {label} searching keyword "{keyword}"')
            sender = ctx.sender()
            outcome = sender.send(label, bot, Content(text="/start"))
            if not outcome.ok:
                print(f"Account {label} failed to resolve bot.")
                keyword_failed = True
                record(ctx, task, label, keyword, False, "BOT_FAIL", outcome.code)
                continue
            reply = _bot_reply(ctx, label, bot, keyword)
            if reply is None:
                print(f"Account {label} no response #1")
                record(ctx, task, label, keyword, False, "NO_RESPONSE", "")
                continue
            links = links_of(reply)
            if links:
                write_outputs(out_path, links, keyword)
                collected[keyword] = collected.get(keyword, 0) + len(links)
                total_links += len(links)
                print(f'Account {label} collected {len(links)} links for "{keyword}"')
                record(
                    ctx,
                    task,
                    label,
                    keyword,
                    True,
                    "COLLECTED",
                    f"{len(links)} link(s)",
                )
            else:
                print(f'Account {label} collected 0 links for "{keyword}"')
                record(ctx, task, label, keyword, True, "EMPTY", "")
        if stopped_reason:
            break
    if not stopped_reason:
        print("All keywords processed.")
    if ctx.live:
        print("Collection stopped.")
    print(f"collected: {total_links} link(s) -> {out_path}")
    close_task(ctx, task, ok=total_links, failed=1 if keyword_failed else 0)
    return 0 if not keyword_failed else 1


def _bot_reply(ctx: RunContext, label: str, bot, keyword: str) -> str | None:
    """The bot's answer after the keyword is sent, read from its chat.

    Live reads the bot chat's newest message through the same history
    read the news monitor uses; the page's own wait (:1747421) sits
    between the send and the read.
    """
    from ._common import pause as _pause

    sender = ctx.sender()
    outcome = sender.send(label, bot, Content(text=keyword))
    if not outcome.ok:
        return None
    _pause(ctx, 1.0)
    result = sender.history(label, bot, minutes=5, limit=1)
    if not result.ok:
        return None
    texts = [part.split("|", 2)[-1] for part in (result.items or [])]
    return texts[0] if texts else None


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(CollectConfig.from_dict(raw, ctx.settings), ctx)


__all__ = [
    "COLLECT_COMMAND",
    "COLLECT_MODE",
    "CollectConfig",
    "JISOU_BOT",
    "NEXT_PAGE_WORDS",
    "links_of",
    "next_page_button",
    "parse_buttons",
    "run",
    "write_outputs",
]
