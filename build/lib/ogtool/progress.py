"""Progress and counters in the reference client's own wording.

Every batch page shows the same five counters (``processed / total``, plus
``ok``, ``failed`` and ``skipped``); keeping them in one place means the dry
run and the live run print the same line, and a later port back into the
client copies one shape instead of eleven.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Stats:
    """The five numbers a batch page shows, plus a per-code tally."""

    total: int = 0
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    codes: dict[str, int] = field(default_factory=dict)

    @property
    def processed(self) -> int:
        return self.ok + self.failed + self.skipped

    def record(self, ok: bool, code: str = "") -> None:
        if ok:
            self.ok += 1
            return
        self.failed += 1
        if code:
            self.codes[code] = self.codes.get(code, 0) + 1

    def skip(self) -> None:
        self.skipped += 1

    def lines(self, processed_label: str = "Processed") -> list[str]:
        """The page's counters, one per line, in the order it shows them."""
        out = [f"{processed_label}: {self.processed} / {self.total}"]
        out.append(f"ok: {self.ok}")
        out.append(f"failed: {self.failed}")
        out.append(f"skipped: {self.skipped}")
        for code in sorted(self.codes):
            out.append(f"{code}: {self.codes[code]}")
        return out

    def summary(self) -> str:
        return (f"processed={self.processed}/{self.total} ok={self.ok} "
                f"failed={self.failed} skipped={self.skipped}")


def buckets(names: dict[str, str]) -> dict[str, list[str]]:
    """One empty bucket per key, for the classified pages."""
    return {key: [] for key in names}


def render_buckets(names: dict[str, str],
                   values: dict[str, list[str]]) -> list[str]:
    """``ok: 3`` style lines, using the label the page carries."""
    return [f"{names[key]}：{len(values.get(key) or [])}" for key in names]
