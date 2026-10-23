"""Per task logging: one file per day under ``logs/<task>/YYYYMMDD.log``."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def task_logger(
    log_dir: Path | str,
    task: str,
    *,
    echo: bool = True,
    level: int = logging.INFO,
) -> logging.Logger:
    """Return a logger writing to the task file and, optionally, the console.

    The file name is the day the logger was created; a long running task keeps
    that one file, which matches how the reference client shows one log page
    per task run rather than per calendar day.
    """
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in task)
    directory = Path(log_dir).expanduser() / (safe or "default")
    directory.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    path = directory / f"{day}.log"

    logger = logging.getLogger(f"ogtool.{safe}")
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(_FORMAT, _DATEFMT)
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if echo:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(stream)

    logger.debug("log file: %s", path)
    return logger


def close_logger(logger: logging.Logger) -> None:
    """Flush and detach every handler, so tests leave no open files behind."""
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
