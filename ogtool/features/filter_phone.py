"""#12 filter phones - sort a phone list into the page's four buckets.

Fields: the number list, the number of concurrent accounts, the four counters
(valid / invalid / active / restricted), ``Processed 0 / 0``, the current number, the
status and the output file.

The bucket comes straight off the answer, the same way the reference client
sorts it: a resolved number is valid, an answer that only says "active" is an
active account, a privacy wall makes it restricted, everything else is invalid.

The page's other fields are here too: three input sources (a file, typed lines,
or a raw blob), ``deepScan``, the two intervals (``singleInterval`` between two
numbers on one account, ``globalInterval`` between any two numbers at all), and
the output folder — with the resolved path printed, because a run that writes
somewhere else than the page says is worse than one that fails.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..config import ConfigError, Settings
from ..flags import Flag
from ..limiter import PeerFloodBackoff, format_duration
from ..progress import Stats
from ..report import export, read_lines, write_lines
from ..runtime import RunContext
from ..senders import USER_PRIVACY_RESTRICTED
from ..simulate import OK
from ..targets import Target, TargetError
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    lanes_by,
    name_of,
    num,
    open_task,
    out_dir,
    pause,
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
    Flag("--raw", "manual_raw", str,
         "the same list as one blob (spaces, commas and newlines all split)"),
    Flag("--source-file", "source_file", str, "alias of --file"),
    Flag("--deep-scan", "deep_scan", None,
         "probe with every bound account instead of one", action="store_true"),
    Flag("--single-interval-ms", "single_interval_ms", int,
         "gap between two numbers on the same account"),
    Flag("--global-interval-ms", "global_interval_ms", int,
         "gap between any two numbers"),
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
    output_dir: str = ""
    flood_cap: int = 2
    input_mode: str = "manual"
    source_file: str = ""
    manual_raw: str = ""
    deep_scan: bool = False
    single_interval_ms: int = 0
    global_interval_ms: int = 0
    task_name: str = "filter-phone"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> PhoneConfig:
        del settings
        data = section(raw, SECTION)
        numbers, mode = read_numbers(data)
        if not numbers:
            raise ConfigError(f"{SECTION}: no numbers given (--numbers/--file)")
        single = num(data, "single_interval_ms", 0, kind=int, minimum=0)
        whole = num(data, "global_interval_ms", 0, kind=int, minimum=0)
        if single and whole and single < whole:
            # The page draws them as two knobs; an inverted pair means the user
            # mixed them up, so say so instead of silently using one.
            raise ConfigError(
                f"{SECTION}: single_interval_ms ({single}) is below "
                f"global_interval_ms ({whole}); the per-account gap cannot be "
                f"tighter than the global one")
        return cls(
            account_labels=accounts_of(raw, data),
            numbers=_unique(numbers),
            file=text(data, "file"),
            threads=num(data, "threads", 1, kind=int, minimum=1),
            output=text(data, "output"),
            output_dir=text(data, "output_dir"),
            flood_cap=num(data, "flood_cap", 2, kind=int),
            input_mode=mode,
            source_file=text(data, "source_file"),
            manual_raw=str(data.get("manual_raw") or ""),
            deep_scan=bool(data.get("deep_scan")),
            single_interval_ms=single,
            global_interval_ms=whole,
            task_name=name_of(raw, data, "filter-phone"),
        )


INPUT_MODES = ("manual", "raw", "file")


def _split_numbers(blob: str) -> list[str]:
    """Numbers out of a blob: commas, semicolons, spaces and newlines all split."""
    out: list[str] = []
    for row in blob.replace(";", "\n").replace(",", "\n").splitlines():
        out.extend(part for part in row.split() if part.strip())
    return [part.strip() for part in out if part.strip()]


def read_numbers(data: dict) -> tuple[list[str], str]:
    """The number list and which of the page's three sources it came from."""
    mode = str(data.get("input_mode") or "").lower()
    if mode and mode not in INPUT_MODES:
        raise ConfigError(
            f"{SECTION}: input_mode must be manual, raw or file, is {mode}")
    typed = _split_numbers(str(data.get("numbers") or ""))
    raw_blob = _split_numbers(str(data.get("manual_raw") or ""))
    file_name = text(data, "source_file") or text(data, "file")
    from_file = read_lines(file_name) if file_name else []

    if not mode:
        mode = "file" if from_file else ("raw" if raw_blob else "manual")
    numbers: list[str] = list(typed)
    if mode == "raw":
        numbers.extend(raw_blob)
    elif mode == "file":
        numbers.extend(from_file)
    return numbers, mode


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = item.strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def phone_target(number: str) -> Target:
    """A number list is always a phone list, so the id-first rule is skipped."""
    text = number.strip()
    if not text:
        raise TargetError("empty phone number")
    return Target("private", text if text.startswith("+") else f"+{text}")


def _deep_bucket(config: PhoneConfig, sender: object, labels: list[str],
                 target: Target, bucket: str, number: str) -> str:
    """``deepScan``: probe with every bound account and keep the disagreement.

    One account answering "invalid" is weak evidence — a number can be hidden
    from that account and visible to another. When the accounts disagree the
    number goes to 受限账号 and the disagreement is printed, because calling a
    number invalid when half the pool can see it is the expensive mistake.
    """
    answers: list[tuple[str, str]] = []
    for label in labels:
        probe = sender.resolve(label, target)  # type: ignore[attr-defined]
        answers.append((label, classify(probe.code, probe.ok)))
    buckets = {name for _, name in answers}
    if len(buckets) == 1:
        return bucket
    detail = ", ".join(f"{label}={name}" for label, name in answers)
    print(f"deep scan: {number} disagrees ({detail}) -> {RESTRICTED}")
    return RESTRICTED


def run(config: PhoneConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts] or ["-"]

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        numbers=len(config.numbers),
        threads=config.threads,
        input_mode=config.input_mode,
        deep_scan="on" if config.deep_scan else "off",
        single_interval=f"{config.single_interval_ms} ms",
        global_interval=f"{config.global_interval_ms} ms",
        output=config.output or "-",
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"numbers": config.numbers}, len(config.numbers))
    sender = ctx.sender()
    stats = Stats(total=len(config.numbers))
    buckets: dict[str, list[str]] = {key: [] for key in BUCKETS}
    current = "-"

    waited = 0.0
    backoff = PeerFloodBackoff()

    def check_number(index: int, number: str, account: str,
                     previous: bool) -> str:
        """One resolve, with the page's ``floodCap`` retries around it."""
        nonlocal waited
        target = phone_target(number)
        if previous:
            gap = max(config.global_interval_ms, config.single_interval_ms)
            waited += gap / 1000.0
            pause(ctx, gap / 1000.0)
        result = sender.resolve(account, target)
        attempts = 0
        while result.code == "PEER_FLOOD" and attempts < config.flood_cap:
            attempts += 1
            delay = backoff.wait_for(attempts - 1)
            print(f"  PEER_FLOOD backoff {delay:.0f}s "
                  f"(attempt {attempts}/{config.flood_cap})")
            waited += delay
            pause(ctx, delay)
            result = sender.resolve(account, target)
        bucket = classify(result.code, result.ok)
        if config.deep_scan and len(labels) > 1:
            bucket = _deep_bucket(config, sender, labels, target, bucket,
                                  number)
        stats.record(result.ok, result.code)
        record(ctx, task, account, number, result.ok, result.code, bucket)
        print(f"[{index:03d}/{stats.total:03d}] {number} -> {bucket}"
              f"{'' if result.ok else ' (' + result.code + ')'}")
        return bucket

    def check_lane(lane: list[tuple[int, str]], position: int) -> list[str]:
        account = labels[position % len(labels)]
        marked: list[str] = []
        for offset, (index, number) in enumerate(lane):
            current = number
            marked.append(check_number(index, number, account, offset > 0))
        del current
        return marked

    work = list(enumerate(config.numbers, start=1))
    lanes = lanes_by(lambda pair: labels[(pair[0] - 1) % len(labels)], work,
                     config.threads)
    marked: dict[int, str] = {}
    if len(lanes) > 1:
        print(f"threads: {len(lanes)} lanes, one account each")
        with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
            futures = [
                pool.submit(check_lane, lane, position)
                for position, lane in enumerate(lanes)
            ]
            for lane, future in zip(lanes, futures, strict=False):
                for (index, _), bucket in zip(lane, future.result(),
                                              strict=False):
                    marked[index] = bucket
    else:
        for (index, _), bucket in zip(work, check_lane(work, 0),
                                      strict=False):
            marked[index] = bucket
    for index, number in enumerate(config.numbers, start=1):
        buckets[marked.get(index, INVALID)].append(number)
    current = config.numbers[-1] if config.numbers else "-"

    for name in BUCKETS:
        print(f"{name}：{len(buckets[name])}")
    for line in stats.lines("Processed"):
        print(line)
    print(f"当前号码：{current}  状态：{'空闲' if not stats.failed else '完成'}")

    if waited:
        print(f"waited: {format_duration(waited)}")
    base = out_dir(ctx, {"output_dir": config.output_dir}, ctx.settings)
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
           "TITLE", "VALID", "classify", "main", "phone_target", "run"]
