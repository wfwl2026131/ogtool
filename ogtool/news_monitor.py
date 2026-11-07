"""The external news monitor (AiChatNewsMonitor / pollNextChannel).

The AI chat page's own news injector (:2275010), configured per channel
line (:1744986 ``One channel per line: link|filterWords|containsWords
(words separated by ,)``) with an insert probability (:1744985 ``Insert
probability (%)``) and a focus decay (:1738480-1738482
``newsFocusInitialPercent / newsFocusHalfLifeMinutes /
newsFocusMaxAgeSeconds``).  The monitor watches the channel, judges each
item, and the chat injects what survived - the page's own lines say
what happened to it (:1738365, :1744957-1744964).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

#: The page's verdicts (:1738365, :1738367, :1738372, :1744957-1744964).
LINE_MISSING_LINK = "External news channel line ignored: missing link."
LINE_RECEIVED = "[G%1] external news received: src=%2 msgId=%3 text=%4"
LINE_QUEUED = "External news queued"
LINE_PENDING = "External news pending: no joined accounts"
LINE_SKIPPED = (
    "External news skipped by filters: stale=%1 filtered=%2 contains_miss=%3 (%4)"
)
LINE_JOIN_FAILED = "External news: join/resolve failed: %1"
LINE_API_FAILED = "External news: chat api failed: %1"
LINE_EMPTY_SUMMARY = "External news: empty summary."
LINE_INJECTED = "External news injected: %1"
LINE_DISABLED = "External news channels are disabled by settings."
LINE_CHANNELS = "External news channels:"

#: The focus decay (:1738480-1738482), with the page's defaults.
FOCUS_INITIAL_PERCENT = 100
FOCUS_HALF_LIFE_MIN = 60.0
FOCUS_MAX_AGE_SEC = 86400.0


@dataclass(slots=True)
class NewsChannel:
    """One configured channel line (:1744986)."""

    link: str
    filter_words: tuple[str, ...] = ()
    contains_words: tuple[str, ...] = ()

    @property
    def has_filters(self) -> bool:
        return bool(self.filter_words or self.contains_words)


def parse_channel_line(line: str) -> tuple[NewsChannel | None, str]:
    """``link|filterWords|containsWords`` -> a channel, or the missing link.

    A line with no link is ignored whole (:1744957); the two word lists
    are optional and comma separated.  A plain link without pipes is a
    channel with no filters - the reference's own default for a bare line.
    """
    parts = [part.strip() for part in (line or "").strip().split("|")]
    link = parts[0] if parts else ""
    if not link:
        return None, LINE_MISSING_LINK
    filter_words = _words(parts[1]) if len(parts) > 1 else ()
    contains_words = _words(parts[2]) if len(parts) > 2 else ()
    return NewsChannel(
        link=link, filter_words=filter_words, contains_words=contains_words
    ), ""


def _words(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(slots=True)
class NewsItem:
    """One item the monitor received (:1738365)."""

    source: str
    message_id: int
    text: str
    age_sec: float = 0.0

    @property
    def key(self) -> str:
        """A stable identity, so the same item is not judged twice."""
        digest = hashlib.sha1(
            f"{self.source}|{self.message_id}|{self.text}".encode()
        ).hexdigest()[:12]
        return digest


def skip_counts(
    items: list[NewsItem], channel: NewsChannel, max_age_sec: float = 0.0
) -> tuple[list[NewsItem], dict[str, int]]:
    """The page's three filters, counted the way its line counts them.

    :1744958 ``External news skipped by filters: stale=%1 filtered=%2
    contains_miss=%3 (%4)``: stale is older than the max age (0 = no age
    check), filtered means a filter word appeared in the text (an exclude
    list), contains_miss means a contains word did not (a required list).
    """
    kept: list[NewsItem] = []
    stale = filtered = contains_miss = 0
    for item in items:
        lowered = item.text.casefold()
        if max_age_sec and item.age_sec > max_age_sec:
            stale += 1
            continue
        if any(word.casefold() in lowered for word in channel.filter_words):
            filtered += 1
            continue
        if channel.contains_words and not any(
            word.casefold() in lowered for word in channel.contains_words
        ):
            contains_miss += 1
            continue
        kept.append(item)
    return kept, {
        "stale": stale,
        "filtered": filtered,
        "contains_miss": contains_miss,
    }


def skipped_line(counts: dict[str, int], total: int) -> str:
    """The page's own summary of one filter pass (:1744958)."""
    return (
        LINE_SKIPPED.replace("%1", str(counts.get("stale", 0)))
        .replace("%2", str(counts.get("filtered", 0)))
        .replace("%3", str(counts.get("contains_miss", 0)))
        .replace("%4", str(total))
    )


def focus_percent(
    item: NewsItem,
    initial: int = FOCUS_INITIAL_PERCENT,
    half_life_min: float = FOCUS_HALF_LIFE_MIN,
    max_age_sec: float = FOCUS_MAX_AGE_SEC,
) -> int:
    """The item's current focus, decaying by half-life (:1738480-1738482).

    Fresh items sit at the initial percent; every half-life halves it;
    an item past the max age has no focus left at all.
    """
    if max_age_sec and item.age_sec > max_age_sec:
        return 0
    if half_life_min <= 0:
        return initial
    half_lives = (item.age_sec / 60.0) / half_life_min
    decayed = initial * (0.5**half_lives)
    return max(0, min(initial, round(decayed)))


def injected(item: NewsItem, percent: int, seed: str = "") -> bool:
    """The inject decision at a given percent, stable per item.

    The reference rolls per item; a stable roll keeps a dry run
    reproducible while still spreading items across the percent scale.
    """
    if percent >= 100:
        return True
    if percent <= 0:
        return False
    digest = hashlib.sha1(f"{item.key}|{seed}".encode()).hexdigest()
    roll = int(digest[:8], 16) % 100
    return roll < percent


__all__ = [
    "FOCUS_HALF_LIFE_MIN",
    "FOCUS_INITIAL_PERCENT",
    "FOCUS_MAX_AGE_SEC",
    "LINE_API_FAILED",
    "LINE_CHANNELS",
    "LINE_DISABLED",
    "LINE_EMPTY_SUMMARY",
    "LINE_INJECTED",
    "LINE_JOIN_FAILED",
    "LINE_MISSING_LINK",
    "LINE_PENDING",
    "LINE_QUEUED",
    "LINE_RECEIVED",
    "LINE_SKIPPED",
    "NewsChannel",
    "NewsItem",
    "focus_percent",
    "injected",
    "parse_channel_line",
    "skip_counts",
    "skipped_line",
]
