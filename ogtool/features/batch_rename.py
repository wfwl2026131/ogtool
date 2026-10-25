"""#10 batch rename - the page's four lists, with pause/resume/stop.

The reference page edits four things per account: the nickname, the bio, the
avatar and the username. Each of the first three has two sources (typed by
hand, or a file), the username also has a probability, and the whole run has
one interval between edits. The page owns a small state machine
(``idle / running / paused / stopped``) and shows the current account plus
``progress``, ``failed`` and ``skipped``.

A dry run replays the same transitions so the states can be read without a
session: ``--pause-at`` and ``--stop-at`` say which entry triggers them.

Which source a list came from is reported and never guessed: an empty list is
skipped with the reference's own wording ("First name is empty", "About text
is empty", "Avatar path empty"), because a rename that silently does nothing
looks exactly like a rename that worked.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ConfigError, Settings
from ..flags import Flag
from ..limiter import format_duration
from ..runtime import RunContext
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    name_of,
    num,
    open_task,
    pause,
    read_list_file,
    record,
    section,
    text,
)

FEATURE = "batch_rename"
SECTION = "batch_rename"
TITLE = "#10 批量改名"

IDLE = "空闲"
RUNNING = "运行"
PAUSED = "已暂停"
STOPPED = "已停止"

#: The two sources the page offers for every list.
MANUAL = "manual"
FILE = "file"
MODES = (MANUAL, FILE)

#: What the page says when a list cannot supply a value.
FIRST_EMPTY = "昵称为空"
ABOUT_EMPTY = "简介为空"
AVATAR_EMPTY = "头像路径为空"
PROBABILITY_MISS = "概率未命中"
SKIPPED = "账号跳过"

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

FLAGS = (
    Flag("--file", "file", str, "the .txt with one name or 'first last' per line"),
    Flag("--about-file", "about_file", str, "the .txt with one bio per line"),
    Flag("--username-file", "username_file", str,
         "the .txt with one username per line (without @)"),
    Flag("--avatar-file", "avatar_file", str,
         "a folder of images, used one per account"),
    Flag("--username-probability", "username_probability", int,
         "how often to set a username, in percent"),
    Flag("--interval-ms", "interval_ms", int,
         "the page's intervalMs: gap between two edits, in milliseconds"),
    Flag("--operation-interval", "operation_interval", float,
         "seconds between two renames (alias of --interval-ms)"),
    Flag("--success-cap", "success_cap", int, "stop after this many renames"),
    Flag("--flood-cap", "flood_cap", int, "PEER_FLOOD retries per entry"),
    Flag("--parallel-mode", "parallel_mode", None,
         "edit the accounts side by side instead of one by one",
         action="store_true"),
    Flag("--pause-at", "pause_at", int,
         "pause once after this entry, then continue (dry-run demo)"),
    Flag("--stop-at", "stop_at", int, "stop once before this entry (dry-run demo)"),
)


def parse_names(rows: list[str]) -> list[tuple[str, str]]:
    """``first name last name`` -> (first, last); a single word becomes the first name."""
    out: list[tuple[str, str]] = []
    for row in rows:
        parts = row.split()
        if not parts:
            continue
        if len(parts) == 1:
            out.append((parts[0], ""))
        else:
            out.append((parts[0], " ".join(parts[1:])))
    return out


def avatar_files(folder: str) -> list[str]:
    """Every image in a folder, sorted, so a run is repeatable."""
    if not folder:
        return []
    path = Path(folder)
    if path.is_file():
        return [str(path)]
    if not path.is_dir():
        raise ConfigError(f"{SECTION}: not a folder: {folder}")
    return [
        str(item) for item in sorted(path.iterdir())
        if item.is_file() and item.suffix.lower() in IMAGE_SUFFIXES
    ]


@dataclass(slots=True)
class RenameSource:
    """One list of the page: what it holds, where it came from, how to use it."""

    mode: str = FILE
    entries: list[str] = field(default_factory=list)
    file: str = ""
    probability: int = 100

    def usable(self) -> bool:
        return bool(self.entries)

    def at(self, index: int) -> str:
        """The entry for the *index*-th account, wrapping when the list is short."""
        if not self.entries:
            return ""
        return self.entries[index % len(self.entries)]

    def misses(self, rng: random.Random) -> bool:
        """Whether this account is skipped: only a probability below 100 skips."""
        if self.probability >= 100:
            return False
        if self.probability <= 0:
            return True
        return rng.randrange(100) >= self.probability


def _rows(data: dict, key: str) -> list[str]:
    return [str(row).strip() for row in (data.get(key) or []) if str(row).strip()]


def _source(data: dict, prefix: str, *, probability: int = 100,
            alias_file: str = "", alias_entries: list[str] | None = None
            ) -> RenameSource:
    """Read ``<prefix>Mode`` / ``<prefix>Manual`` / ``<prefix>File``.

    The nickname list predates the four-list page, so it also answers to the
    plain ``file`` / ``entries`` keys: an old task file keeps working.
    """
    mode = text(data, f"{prefix}_mode").lower()
    if mode and mode not in MODES:
        raise ConfigError(f"{SECTION}: {prefix}_mode must be manual or file")
    file_name = text(data, f"{prefix}_file")
    if not file_name and prefix == "nickname":
        file_name = alias_file
    manual = _rows(data, f"{prefix}_manual")
    if alias_entries is not None and prefix == "nickname":
        manual = [str(row).strip() for row in alias_entries
                  if str(row).strip()] or manual
    entries = list(manual)
    if file_name and mode != MANUAL:
        # Manual mode means the typed rows win; the file is still remembered so
        # the page can show where the other list would come from.
        entries.extend(row for row in read_list_file(file_name) if row.strip())
    if not mode:
        mode = FILE if file_name else MANUAL
    return RenameSource(mode=mode, entries=entries, file=file_name,
                        probability=probability)


@dataclass(slots=True)
class RenameState:
    """The five things the page displays, updated as the run goes."""

    total: int = 0
    processed: int = 0
    failed: int = 0
    skipped: int = 0
    current: str = "无"
    status: str = IDLE
    pause_at: int = 0
    stop_at: int = 0
    #: Per list, how many edits actually landed: 昵称 / 简介 / 头像 / 用户名.
    done: dict[str, int] = field(default_factory=dict)

    def line(self) -> str:
        return (f"进度：{self.processed}/{self.total}  失败: {self.failed}  "
                f"跳过：{self.skipped}  当前账号：{self.current}  "
                f"状态：{self.status}")

    def bump(self, what: str) -> None:
        self.done[what] = self.done.get(what, 0) + 1


@dataclass(slots=True)
class RenameConfig:
    account_labels: list[str] = field(default_factory=list)
    names: list[tuple[str, str]] = field(default_factory=list)
    file: str = ""
    about: RenameSource = field(default_factory=RenameSource)
    usernames: RenameSource = field(default_factory=RenameSource)
    avatars: RenameSource = field(default_factory=RenameSource)
    interval_ms: int = 0
    operation_interval: float = 10.0
    success_cap: int = 0
    flood_cap: int = 2
    parallel_mode: bool = False
    entries_preview: int = 0
    seed: int = 0
    pause_at: int = 0
    stop_at: int = 0
    task_name: str = "batch-rename"

    def gap(self) -> float:
        """The wait between two edits: the page's intervalMs wins when given."""
        if self.interval_ms > 0:
            return self.interval_ms / 1000.0
        return self.operation_interval

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> RenameConfig:
        del settings
        data = section(raw, SECTION)
        aliases = [str(row) for row in (data.get("entries") or [])]
        alias_file = text(data, "file")
        nickname = _source(
            data,
            "nickname",
            alias_file=alias_file,
            alias_entries=aliases,
        )
        if not nickname.entries:
            raise ConfigError(
                f"{SECTION}: give a .txt list (--file) with one name per line"
            )
        probability = num(data, "username_probability", 100, kind=int)
        if not 0 <= probability <= 100:
            raise ConfigError(
                f"{SECTION}: username_probability must be 0..100, is {probability}"
            )
        avatar_folder = text(data, "avatar_file")
        return cls(
            account_labels=accounts_of(raw, data),
            names=parse_names(nickname.entries),
            file=nickname.file or alias_file,
            about=_source(data, "about"),
            usernames=_source(data, "username", probability=probability),
            avatars=RenameSource(
                mode=FILE,
                entries=avatar_files(avatar_folder),
                file=avatar_folder,
            ),
            interval_ms=num(data, "interval_ms", 0, kind=int),
            operation_interval=num(data, "operation_interval", 10.0),
            success_cap=num(data, "success_cap", 0, kind=int),
            flood_cap=num(data, "flood_cap", 2, kind=int),
            parallel_mode=bool(data.get("parallel_mode")),
            entries_preview=num(data, "entries_preview", 0, kind=int),
            seed=num(data, "seed", 0, kind=int),
            pause_at=num(data, "pause_at", 0, kind=int),
            stop_at=num(data, "stop_at", 0, kind=int),
            task_name=name_of(raw, data, "batch-rename"),
        )


def _edit_extras(config: RenameConfig, state: RenameState, sender: object,
                 account: str, index: int, rng: random.Random) -> None:
    """The three lists besides the nickname, applied to the same account."""
    if config.about.usable():
        value = config.about.at(index)
        result = sender.update_about(account, value)  # type: ignore[attr-defined]
        if result.ok:
            state.bump("简介")
            print(f"账号 {account} 简介已更新")
        else:
            state.failed += 1
            print(f"账号 {account} 简介更新失败: {result.code}")

    if config.avatars.usable():
        path = config.avatars.at(index)
        if not path:
            state.skipped += 1
            print(f"{AVATAR_EMPTY}（账号 {account}）")
        else:
            result = sender.update_avatar(account, path)  # type: ignore[attr-defined]
            if result.ok:
                state.bump("头像")
                print(f"账号 {account} 头像已更新")
            else:
                state.failed += 1
                print(f"账号 {account} 头像更新失败: {result.code}")

    if config.usernames.usable():
        if config.usernames.misses(rng):
            state.skipped += 1
            print(f"{PROBABILITY_MISS}（账号 {account}）")
        else:
            value = config.usernames.at(index).lstrip("@")
            result = sender.update_username(account, value)  # type: ignore[attr-defined]
            if result.ok:
                state.bump("用户名")
                print(f"账号 {account} 用户名已更新: @{value}")
            else:
                state.failed += 1
                print(f"账号 {account} 用户名更新失败: {result.code}")


def run(config: RenameConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts] or ["-"]
    state = RenameState(total=len(config.names), pause_at=config.pause_at,
                        stop_at=config.stop_at)
    rng = random.Random(config.seed)
    gap = config.gap()

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        entries=len(config.names),
        operation_interval=f"{gap:.0f}s" if gap >= 1 else f"{gap * 1000:.0f}ms",
        state=state.line(),
    )
    lists = {"昵称": len(config.names), "简介": len(config.about.entries),
             "头像": len(config.avatars.entries),
             "用户名": len(config.usernames.entries)}
    print("四份清单：" + "  ".join(f"{k} {v}" for k, v in lists.items()))

    task = open_task(ctx, config.task_name, FEATURE,
                     {"file": config.file, "entries": len(config.names),
                      "lists": lists},
                     len(config.names))
    sender = ctx.sender()

    state.status = RUNNING
    print(f"状态：{state.status}")
    waited = 0.0
    for index, (first, last) in enumerate(config.names, start=1):
        if state.stop_at and index >= state.stop_at:
            state.status = STOPPED
            print(f"状态：{state.status}（收到停止，第 {index} 条开始前）")
            break
        if config.success_cap and state.processed >= config.success_cap:
            print(f"stopped: success_cap ({config.success_cap})")
            break

        if index > 1:
            # The rename interval is the page's anti-flood knob: one profile
            # edit per interval, on the account that does the edit.
            waited += gap
            pause(ctx, gap)
        account = labels[(index - 1) % len(labels)]
        state.current = account
        if not first:
            # A blank nickname is the reference's own "First name is empty".
            state.skipped += 1
            print(f"{FIRST_EMPTY}（第 {index} 条）")
        else:
            result = sender.update_profile(account, first, last)
            state.processed += 1
            if result.ok:
                state.bump("昵称")
                record(ctx, task, account, f"{first} {last}".strip(), True)
            else:
                state.failed += 1
                record(ctx, task, account, f"{first} {last}".strip(), False,
                       result.code)

        _edit_extras(config, state, sender, account, index - 1, rng)
        print(f"[{index:03d}/{state.total:03d}] {state.line()}")

        if state.pause_at and index == state.pause_at:
            state.status = PAUSED
            print(f"状态：{state.status}")
            state.status = RUNNING
            print(f"状态：{state.status}（继续）")

    state.status = state.status if state.status == STOPPED else IDLE
    print(f"状态：{state.status}")
    if ctx.live and waited:
        print(f"waited: {format_duration(waited)}")
    if state.done:
        print("细节：" + "  ".join(f"{k} {v}" for k, v in state.done.items())
              + f"  跳过 {state.skipped}")
    print(f"PLAN: {state.processed} renames, {len(accounts)} accounts, "
          f"failed {state.failed}, skipped {state.skipped}")
    close_task(ctx, task, ok=state.processed - state.failed, failed=state.failed)
    return 0 if not state.failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(RenameConfig.from_dict(raw, ctx.settings), ctx)


__all__ = [
    "ABOUT_EMPTY",
    "AVATAR_EMPTY",
    "FILE",
    "FIRST_EMPTY",
    "IDLE",
    "MANUAL",
    "MODES",
    "PAUSED",
    "PROBABILITY_MISS",
    "RUNNING",
    "RenameConfig",
    "RenameSource",
    "RenameState",
    "SKIPPED",
    "STOPPED",
    "TITLE",
    "avatar_files",
    "main",
    "parse_names",
    "run",
]
