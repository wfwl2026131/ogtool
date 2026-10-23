"""#12 filter phones - sort a phone list into the page's four buckets.

Fields: the number list, the number of concurrent accounts, the four counters
(valid / invalid / active / restricted), ``Processed 0 / 0``, the current number, the
status and the output file.

The bucket comes straight off the answer, the same way the reference client
sorts it: a resolved number is valid, an answer that only says "active" is an
active account, a privacy wall makes it restricted, everything else is invalid.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import ConfigError, Settings
from ..flags import Flag
from ..progress import Stats
from ..report import export, read_lines, write_lines
from ..runtime import RunContext
from ..senders import USER_PRIVACY_RESTRICTED
from ..simulate import OK
from ..targets import parse_many
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    name_of,
    num,
    open_task,
    out_dir,
    record,
    section,
    text,
)

FEATURE = "filter_phone"
SECTION = "filter_phone"
TITLE = "#12 筛选手机"

VALID = "有效"
INVALID = "无效"
ACTIVE = "活跃账号"
RESTRICTED = "受限账号"

BUCKETS = {VALID: VALID, INVALID: INVALID, ACTIVE: ACTIVE, RESTRICTED: RESTRICTED}

FLAGS = (
    Flag("--numbers", "numbers", str, "phone numbers, comma separated"),
    Flag("--file", "file", str, "file with one number per line"),
    Flag("--threads", "threads", int, "concurrent accounts"),
    Flag("--output", "output", str, "where the classified list goes"),
    Flag("--flood-cap", "flood_cap", int, "PEER_FLOOD retries per number"),
)


def classify(code: str, ok: bool) -> str:
    """Which of the four buckets this answer belongs to."""
    if ok and code == "ACTIVE":
        return ACTIVE
    if ok and code in (OK, ""):
        return VALID
    if code == USER_PRIVACY_RESTRICTED:
        return RESTRICTED
    return INVALID


@dataclass(slots=True)
class PhoneConfig:
    account_labels: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    file: str = ""
    threads: int = 1
    output: str = ""
    flood_cap: int = 2
    task_name: str = "filter-phone"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> PhoneConfig:
        del settings
        data = section(raw, SECTION)
        numbers = [item.strip() for item in
                   str(data.get("numbers") or "").replace(",", "\n").splitlines()
                   if item.strip()]
        file_name = text(data, "file")
        if file_name:
            numbers.extend(read_lines(file_name))
        if not numbers:
            raise ConfigError(f"{SECTION}: no numbers given (--numbers/--file)")
        return cls(
            account_labels=accounts_of(raw, data),
            numbers=_unique(numbers),
            file=file_name,
            threads=num(data, "threads", 1, kind=int, minimum=1),
            output=text(data, "output"),
            flood_cap=num(data, "flood_cap", 2, kind=int),
            task_name=name_of(raw, data, "filter-phone"),
        )


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = item.strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def run(config: PhoneConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts] or ["-"]

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        numbers=len(config.numbers),
        threads=config.threads,
        output=config.output or "-",
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"numbers": config.numbers}, len(config.numbers))
    sender = ctx.sender()
    stats = Stats(total=len(config.numbers))
    buckets: dict[str, list[str]] = {key: [] for key in BUCKETS}
    current = "-"

    for index, number in enumerate(config.numbers, start=1):
        current = number
        account = labels[(index - 1) % len(labels)]
        target = parse_many([number], "private")[0]
        result = sender.resolve(account, target)
        bucket = classify(result.code, result.ok)
        buckets[bucket].append(number)
        stats.record(result.ok, result.code)
        record(ctx, task, account, number, result.ok, result.code, bucket)
        print(f"[{index:03d}/{stats.total:03d}] {number} -> {bucket}"
              f"{'' if result.ok else ' (' + result.code + ')'}")

    for name in BUCKETS:
        print(f"{name}：{len(buckets[name])}")
    for line in stats.lines("Processed"):
        print(line)
    print(f"当前号码：{current}  状态：{'空闲' if not stats.failed else '完成'}")

    base = out_dir(ctx, {"output_dir": ""}, ctx.settings)
    path = config.output or f"{base}/filter_phone.tsv"
    rows = [f"{number}\t{name}" for name in BUCKETS for number in buckets[name]]
    write_lines(path, rows, header="number\tbucket")
    print(f"输出文件：{path}")
    export(f"{base}/filter_phone_summary.txt",
           [f"{name}: {len(buckets[name])}" for name in BUCKETS], "summary")
    print(f"PLAN: {len(config.numbers)} numbers, {len(accounts)} accounts, "
          f"valid {len(buckets[VALID])}")
    close_task(ctx, task, ok=stats.ok, failed=stats.failed)
    return 0


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(PhoneConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["ACTIVE", "BUCKETS", "INVALID", "PhoneConfig", "RESTRICTED",
           "TITLE", "VALID", "classify", "main", "run"]
