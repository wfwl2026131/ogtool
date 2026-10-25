"""The frozen-account check, done the way the reference does it: through @spambot.

The reference's own exit route for frozen accounts is a bot conversation, not a
login task. The dump carries the pieces next to each other (lines 1742586-1742592
of ``/tmp/og-strings.txt``)::

     @spambot
     @spambot
     /start
    code: %2
     /start
     @spambot

and the routine above them narrates the loop: ``Collecting frozen accounts``,
``Detected frozen account #%1 (index %2)``, ``Processing frozen account #%1
(index %2, phone %3)``. So: send ``/start``, read what the bot answers, decide.

**What the bot answers is not in the dump.** The three verdicts below are
Telegram's own wording for @spambot, not the reference's strings, so the
classification is marked 【推测】 in the report and every run prints the reply
text first — the verdict is a convenience on top of the evidence, never a
replacement for it.
"""

from __future__ import annotations

#: The bot the reference talks to, exactly as the dump spells it.
SPAMBOT = "spambot"
#: What it sends, also from the dump.
START = "/start"

#: Reply markers per verdict. Telegram's @spambot wording, kept short and
#: lower-cased; a reply that matches none of them stays unclassified.
FREE_MARKERS = ("no limits", "free as a bird", "good news")
HARSH_MARKERS = ("harsh response", "consider it a warning")
LIMITED_MARKERS = ("now limited", "limited until", "can't be used",
                   "cannot be used", "restricted")

VERDICTS = ("two_way", "one_way", "frozen")


def classify(reply: str) -> tuple[str, str]:
    """``(kind, marker)`` for a @spambot reply; ``("", "")`` when unsure.

    The order matters: a limited account's reply mentions the limits, and the
    "harsh response" text is the middle state, so it is checked before the free
    wording but after the hard limit.
    """
    text = (reply or "").lower()
    for marker in LIMITED_MARKERS:
        if marker in text:
            return "frozen", marker
    for marker in HARSH_MARKERS:
        if marker in text:
            return "one_way", marker
    for marker in FREE_MARKERS:
        if marker in text:
            return "two_way", marker
    return "", ""


def headline(kind: str) -> str:
    """One line per verdict, in the report's vocabulary."""
    if kind == "two_way":
        return "healthy: no limits on this account"
    if kind == "one_way":
        return "one-way: can write to contacts, not to strangers"
    if kind == "frozen":
        return "frozen: the account is limited and cannot act"
    return "unknown: the reply matched none of the known markers"


def shorten(reply: str, limit: int = 200) -> str:
    """The reply, one line, cut — the text is evidence, so it is shown."""
    flat = " ".join((reply or "").split())
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


__all__ = ["FREE_MARKERS", "HARSH_MARKERS", "LIMITED_MARKERS", "SPAMBOT",
           "START", "VERDICTS", "classify", "headline", "shorten"]
