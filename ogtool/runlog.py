"""The run log: everything a run prints, kept on disk, readable live.

The features print progress straight to the console (``sent ...``, ``failed
...``, backoff notes). That is great in the terminal and gone afterwards. The
run log tees the console output into ``logs/runs/<command>-<ts>.log``, so a
run can be watched later or followed from a second terminal while it works:

    ogtool logs                  # the newest runs
    ogtool logs tail             # last 50 lines of the newest
    ogtool logs tail --follow    # stream until Ctrl+C
"""

from __future__ import annotations

import io
import sys
import time
from datetime import datetime
from pathlib import Path

RUNS_DIR = "runs"


def runs_dir(log_dir: Path | str) -> Path:
    """Where run transcripts live, next to the task logs."""
    return Path(log_dir).expanduser() / RUNS_DIR


class Tee(io.TextIOBase):
    """One stream in, one terminal out, one file kept.

    Partial writes are buffered until a newline arrives, so a line is never
    split across two timestamps. The prefix is a plain ``HH:MM:SS``; the
    terminal still gets the line exactly as the feature printed it.
    """

    def __init__(self, stream: io.TextIOBase, file: io.TextIOBase,
                 *, prefix: bool = True) -> None:
        self.stream = stream
        self.file = file
        self.prefix = prefix
        self._pending = ""

    def writable(self) -> bool:  # pragma: no cover - trivial
        return True

    def write(self, text: str) -> int:
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self._emit(line)
        return len(text)

    def _emit(self, line: str) -> None:
        stamped = (f"{datetime.now():%H:%M:%S} {line}"
                   if self.prefix else line)
        self.file.write(stamped + "\n")
        self.file.flush()
        self.stream.write(line + "\n")
        self.stream.flush()

    def flush(self) -> None:
        if self.file.closed:
            return
        self.file.flush()
        self.stream.flush()

    def close_run(self) -> None:
        """Emit a trailing partial line and flush; the owner closes the file."""
        if self._pending:
            self._emit(self._pending)
            self._pending = ""
        self.flush()


class RunLog:
    """One run's transcript: the file and the stdout it replaced."""

    def __init__(self, path: Path, original: io.TextIOBase) -> None:
        self.path = path
        self.original = original
        self.tee = Tee(original, path.open("a", encoding="utf-8"))

    def __enter__(self) -> RunLog:
        sys.stdout = self.tee
        return self

    def __exit__(self, *_exc: object) -> None:
        self.tee.close_run()
        sys.stdout = self.original
        self.tee.file.close()

    def lines(self) -> int:
        return sum(1 for _ in self.path.open(encoding="utf-8"))


def start(settings: object, command: str) -> RunLog:
    """Open a run log and make stdout flow into it until the run ends."""
    directory = runs_dir(getattr(settings, "log_dir", Path("logs")))
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"{command}-{stamp}.log"
    return RunLog(path, sys.stdout)


def newest(directory: Path | str) -> Path | None:
    """The newest transcript in the runs directory, if any."""
    files = sorted(runs_dir(directory).glob("*.log"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def read_tail(path: Path, count: int = 50) -> list[str]:
    """The last ``count`` lines of a transcript."""
    return path.read_text(encoding="utf-8").splitlines()[-count:]


def follow(path: Path, *, stop_after_idle: float | None = None,
           poll: float = 1.0, printer=print) -> None:
    """Stream a transcript as it grows, like ``tail -f``.

    ``stop_after_idle`` ends the loop after that many seconds without new
    lines - the hook tests use; an operator leaves it off and Ctrl+C stops.
    """
    position = 0
    idle = 0.0
    while True:
        with path.open(encoding="utf-8") as handle:
            handle.seek(position)
            for line in handle:
                printer(line.rstrip("\n"))
            position = handle.tell()
        if stop_after_idle is not None and position < path.stat().st_size:
            idle = 0.0
        elif stop_after_idle is not None:
            idle += poll
            if idle >= stop_after_idle:
                return
        time.sleep(poll)


def is_live_watching(directory: Path | str) -> bool:
    """True when a transcript grew in the last two minutes."""
    newest_path = newest(directory)
    if newest_path is None:
        return False
    age = time.time() - newest_path.stat().st_mtime
    return age < 120
