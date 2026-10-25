"""#8 batch join - join groups with a pool of accounts.

Fields from the reference page: account choice, request interval, request
timeout, per account join interval, per account join cap, concurrency and the
total.  A dry run prints every join it would perform, in the order it would
perform it, with the account that would perform it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial

from ..config import ConfigError, Settings
from ..flags import Flag
from ..progress import Stats
from ..runner import Job, execute, print_outcome
from ..runtime import RunContext
from ..targets import Target
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    name_of,
    num,
    open_task,
    pause,
    record,
    section,
    targets_of,
    text,
)

FEATURE = "batch_join"
SECTION = "batch_join"
TITLE = "#8 批量加群"

FLAGS = (
    Flag("--targets", "targets", str, "group links, comma separated"),
    Flag("--targets-file", "files", str, "file with one link per line",
         action="append"),
    Flag("--request-interval", "request_interval", float,
         "seconds between two requests of the whole pool"),
    Flag("--request-timeout", "request_timeout", float,
         "seconds before a request is called a timeout"),
    Flag("--account-interval", "account_interval", float,
         "seconds between two joins of the same account"),
    Flag("--per-account-cap", "per_account_cap", int,
         "joins one account may perform"),
    Flag("--threads", "threads", int,
         "one lane per account, lanes join at the same time"),
    Flag("--success-cap", "success_cap", int, "stop after this many joins"),
    Flag("--exec-cap", "exec_cap", int, "stop after this many attempts"),
    Flag("--flood-cap", "flood_cap", int, "PEER_FLOOD retries per target"),
    Flag("--operation-type", "operation_type", str, "join or leave"),
    Flag("--leave-targets", "leave_targets", str,
         "groups a leave run works on, comma separated"),
    Flag("--join-mode", "join_mode", str, "the page's joinMode"),
    Flag("--max-join-count", "max_join_count", int, "join count cap"),
    Flag("--per-account-interval-min", "per_account_join_interval_min", float,
         "per-account gap, seconds"),
    Flag("--per-account-interval-max", "per_account_join_interval_max", float,
         "per-account gap upper bound, seconds"),
    Flag("--max-joined-chats", "max_joined_chats_per_account", int,
         "stop joining once an account holds this many groups"),
    Flag("--join-concurrency", "join_concurrency", int, "lanes (overrides --threads)"),
    Flag("--leave-mode", "leave_mode", str, "the page's leaveMode"),
    Flag("--mute-channels", "mute_channels", None, "mute joined channels",
         action="store_true"),
    Flag("--mute-private", "mute_private", None, "mute joined private chats",
         action="store_true"),
    Flag("--mute-interval", "mute_interval", float, "gap between mutes, seconds"),
    Flag("--leave-interval", "leave_interval", float, "gap between leaves, seconds"),
    Flag("--auto-download-private", "auto_download_scope_private", None,
         "auto-download in private chats", action="store_true"),
    Flag("--auto-download-group", "auto_download_scope_group", None,
         "auto-download in groups", action="store_true"),
    Flag("--auto-download-channel", "auto_download_scope_channel", None,
         "auto-download in channels", action="store_true"),
    Flag("--auto-download-photos", "auto_download_photos", None,
         "auto-download photos", action="store_true"),
    Flag("--auto-download-files", "auto_download_files", None,
         "auto-download files", action="store_true"),
    Flag("--auto-download-limit-mb", "auto_download_limit_mb", int,
         "auto-download size cap, MB"),
    Flag("--auto-play-videos", "auto_play_videos", None, "autoplay videos",
         action="store_true"),
    Flag("--auto-play-round-videos", "auto_play_round_videos", None,
         "autoplay round videos", action="store_true"),
    Flag("--auto-play-gifs", "auto_play_gifs", None, "autoplay gifs",
         action="store_true"),
    Flag("--auto-play-limit-mb", "auto_play_limit_mb", int,
         "autoplay size cap, MB"),
)


OPERATIONS = ("join", "leave")


def _operation(data: dict) -> str:
    """``operationType``: join by default, and only those two words."""
    value = str(data.get("operation_type") or "join").strip().lower()
    if value not in OPERATIONS:
        raise ConfigError(
            f"{SECTION}: operation_type must be join or leave, is {value}")
    return value


def _seconds(data: dict, name: str, default: float) -> float:
    """A gap in seconds, accepting the page's ``...Ms`` spelling too.

    The dump carries both ``perAccountJoinIntervalMinSec`` and ``...Ms``; a
    setting that exists twice must mean the same thing in both spellings.
    """
    if data.get(f"{name}_sec") is not None:
        return float(num(data, f"{name}_sec", default))
    if data.get(f"{name}_ms") is not None:
        return float(num(data, f"{name}_ms", default * 1000.0)) / 1000.0
    return default


@dataclass(slots=True)
class JoinConfig:
    account_labels: list[str] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    request_interval: float = 5.0
    request_timeout: float = 30.0
    account_interval: float = 10.0
    per_account_cap: int = 0
    threads: int = 1
    success_cap: int = 0
    exec_cap: int = 0
    flood_cap: int = 2
    # --- batch_join_config.json, by the page's own key names -------------
    #: ``operationType``: join or leave.
    operation_type: str = "join"
    #: ``leaveTargets``: the groups a leave run works on.
    leave_targets: list[Target] = field(default_factory=list)
    #: ``joinMode`` / ``maxJoinCount``.
    join_mode: str = ""
    max_join_count: int = 0
    #: ``perAccountJoinIntervalMinSec`` / ``...MaxSec``: per-account pacing.
    per_account_interval_min: float = 0.0
    per_account_interval_max: float = 0.0
    #: ``maxJoinedChatsPerAccount``: stop joining once an account holds this.
    max_joined_chats: int = 0
    #: ``joinConcurrency``: lanes; falls back to ``threads``.
    join_concurrency: int = 0
    #: ``leaveMode`` / ``muteChannels`` / ``mutePrivate`` and the two gaps.
    leave_mode: str = ""
    mute_channels: bool = False
    mute_private: bool = False
    mute_interval: float = 0.0
    leave_interval: float = 0.0
    #: ``autoDownload*`` / ``autoPlay*``: the per-account media policy that
    #: rides along with the join task.
    auto_download_scope_private: bool = False
    auto_download_scope_group: bool = False
    auto_download_scope_channel: bool = False
    auto_download_photos: bool = False
    auto_download_files: bool = False
    auto_download_limit_mb: int = 0
    auto_play_round_videos: bool = False
    auto_play_videos: bool = False
    auto_play_gifs: bool = False
    auto_play_limit_mb: int = 0
    task_name: str = "batch-join"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> JoinConfig:
        del settings
        data = section(raw, SECTION)
        return cls(
            account_labels=accounts_of(raw, data),
            targets=targets_of(data, kind="group"),
            request_interval=num(data, "request_interval", 5.0),
            request_timeout=num(data, "request_timeout", 30.0),
            account_interval=num(data, "account_interval", 10.0),
            per_account_cap=num(data, "per_account_cap", 0, kind=int),
            threads=num(data, "threads", 1, kind=int, minimum=1),
            success_cap=num(data, "success_cap", 0, kind=int),
            exec_cap=num(data, "exec_cap", 0, kind=int),
            flood_cap=num(data, "flood_cap", 2, kind=int),
            operation_type=_operation(data),
            leave_targets=(targets_of({"targets": data.get("leave_targets")},
                                      kind="group")
                           if data.get("leave_targets") else []),
            join_mode=text(data, "join_mode"),
            max_join_count=num(data, "max_join_count", 0, kind=int,
                               minimum=0),
            per_account_interval_min=_seconds(
                data, "per_account_join_interval_min", 0.0),
            per_account_interval_max=_seconds(
                data, "per_account_join_interval_max", 0.0),
            max_joined_chats=num(data, "max_joined_chats_per_account", 0,
                                 kind=int, minimum=0),
            join_concurrency=num(data, "join_concurrency", 0, kind=int,
                                 minimum=0),
            leave_mode=text(data, "leave_mode"),
            mute_channels=bool(data.get("mute_channels", False)),
            mute_private=bool(data.get("mute_private", False)),
            mute_interval=_seconds(data, "mute_interval", 0.0),
            leave_interval=_seconds(data, "leave_interval", 0.0),
            auto_download_scope_private=bool(
                data.get("auto_download_scope_private", False)),
            auto_download_scope_group=bool(
                data.get("auto_download_scope_group", False)),
            auto_download_scope_channel=bool(
                data.get("auto_download_scope_channel", False)),
            auto_download_photos=bool(data.get("auto_download_photos", False)),
            auto_download_files=bool(data.get("auto_download_files", False)),
            auto_download_limit_mb=num(data, "auto_download_limit_mb", 0,
                                       kind=int, minimum=0),
            auto_play_round_videos=bool(
                data.get("auto_play_round_videos", False)),
            auto_play_videos=bool(data.get("auto_play_videos", False)),
            auto_play_gifs=bool(data.get("auto_play_gifs", False)),
            auto_play_limit_mb=num(data, "auto_play_limit_mb", 0, kind=int,
                                   minimum=0),
            task_name=name_of(raw, data, "batch-join"),
        )


def assign(accounts: list[str], targets: list[Target],
           per_account_cap: int, exec_cap: int) -> list[tuple[str, Target]]:
    """Round robin, honouring the per account cap and the overall one."""
    pairs: list[tuple[str, Target]] = []
    if not accounts or not targets:
        return pairs
    used: dict[str, int] = {account: 0 for account in accounts}
    for position, target in enumerate(targets):
        candidates = [a for a in accounts if not per_account_cap
                      or used[a] < per_account_cap]
        if not candidates:
            break
        account = candidates[position % len(candidates)]
        used[account] += 1
        pairs.append((account, target))
        if exec_cap and len(pairs) >= exec_cap:
            break
    return pairs


def gap(config: JoinConfig, job: Job, previous: Job | None = None,
        leaving: bool = False) -> float:
    """Seconds before one join - or one leave, which has its own number.

    The pool wide request interval always applies; two operations in a row by
    the same account also have to clear that account's own interval, which is
    the longer of the two. ``leaveInterval`` is the leave page's own pace, the
    way ``accountInterval`` is the join page's.
    """
    wait = config.request_interval
    if leaving and config.leave_interval:
        wait = max(wait, config.leave_interval)
    if previous is not None and previous.account == job.account:
        wait = max(wait, config.account_interval)
    return wait


def _media_policy(config: JoinConfig) -> str:
    """The download / autoplay policy in one line, ``-`` when nothing is set."""
    scopes = [name for name, on in (("private", config.auto_download_scope_private),
                                    ("group", config.auto_download_scope_group),
                                    ("channel", config.auto_download_scope_channel))
              if on]
    parts: list[str] = []
    if scopes:
        kinds = [kind for kind, on in (("photos", config.auto_download_photos),
                                       ("files", config.auto_download_files))
                 if on]
        parts.append(f"download {'+'.join(scopes)}"
                     + (f" ({'+'.join(kinds)})" if kinds else ""))
    if config.auto_download_limit_mb:
        parts.append(f"{config.auto_download_limit_mb}MB")
    plays = [kind for kind, on in (("round", config.auto_play_round_videos),
                                   ("video", config.auto_play_videos),
                                   ("gif", config.auto_play_gifs))
             if on]
    if plays:
        parts.append(f"play {'+'.join(plays)}"
                     + (f" {config.auto_play_limit_mb}MB"
                        if config.auto_play_limit_mb else ""))
    return ", ".join(parts) or "-"


def run(config: JoinConfig, ctx: RunContext) -> int:
    leaving = config.operation_type == "leave"
    targets = config.leave_targets if leaving else config.targets
    if not targets:
        # A leave run with no leaveTargets is the same mistake as a join run
        # with no groups, so it fails the same way rather than doing nothing.
        print(f"ERROR: no {'leave ' if leaving else ''}targets "
              f"(use --targets or --targets-file)")
        return 2
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts]
    pairs = assign(labels, targets, config.per_account_cap,
                   config.exec_cap) if labels else []
    if not labels:
        pairs = [("-", target) for target in targets]

    header(config.task_name,
        accounts=f"{len(labels)}"
                 + (" (no account bound, plan only)" if not labels else ""),
        operation=config.operation_type,
        groups=len(targets),
        request_interval=f"{config.request_interval:.0f}s",
        request_timeout=f"{config.request_timeout:.0f}s",
        account_interval=f"{config.account_interval:.0f}s",
        per_account_cap=config.per_account_cap or "-",
        concurrency=config.join_concurrency or config.threads,
        per_account_interval=(
            f"{config.per_account_interval_min:.0f}-"
            f"{config.per_account_interval_max:.0f}s"
            if config.per_account_interval_max
            else f"{config.per_account_interval_min:.0f}s"),
        max_joined=config.max_joined_chats or "-",
        max_join_count=config.max_join_count or "-",
        join_mode=config.join_mode or "-",
        mute=("channels" if config.mute_channels else "")
             + ("+private" if config.mute_private else "") or "off",
        leave_interval=f"{config.leave_interval:.0f}s",
        media_policy=_media_policy(config),
        total=len(pairs),
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"targets": [t.ref for t in config.targets]}, len(pairs))
    sender = ctx.sender()

    def wants_mute(config: JoinConfig, target: Target) -> bool:
        """``muteChannels`` / ``mutePrivate``: which joins get silenced."""
        if target.kind == "channel":
            return config.mute_channels
        return config.mute_private

    def call(job: Job) -> object:
        assert job.target is not None
        if leaving:
            return sender.leave(job.account, job.target)
        result = sender.join(job.account, job.target)
        if getattr(result, "ok", False) and wants_mute(config, job.target):
            if config.mute_interval:
                pause(ctx, config.mute_interval)
            muted = sender.mute(job.account, job.target)  # type: ignore[attr-defined]
            job.extra["mute"] = "ok" if muted.ok else muted.code
        return result

    jobs = [Job(account=account, subject=target.ref, target=target)
            for account, target in pairs]
    outcome = execute(jobs, ctx, call, total=len(jobs),
                      success_cap=config.success_cap, exec_cap=config.exec_cap,
                      flood_cap=config.flood_cap, retry_cap=1, label="group",
                      interval=partial(gap, config, leaving=leaving),
                      lanes=config.join_concurrency or config.threads)

    muted = 0
    for job in jobs:
        if "ok" not in job.extra:
            continue
        record(ctx, task, job.account, job.subject, job.extra["ok"] == "1",
               job.extra["code"])
        mute_state = job.extra.get("mute", "")
        if mute_state:
            muted += mute_state == "ok"
            if mute_state != "ok":
                print(f"mute {job.account} {job.subject} -> {mute_state}")
    stats: Stats = outcome.stats
    if muted or (config.mute_channels or config.mute_private):
        print(f"muted: {muted} chat(s)")
    print_outcome(outcome, eta_per_job=config.account_interval)
    close_task(ctx, task, ok=stats.ok, failed=stats.failed)
    return 0 if not stats.failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(JoinConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["JoinConfig", "TITLE", "assign", "main", "run"]
