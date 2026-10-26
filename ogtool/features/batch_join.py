"""#8 batch join - join groups with a pool of accounts.

Fields from the reference page: account choice, request interval, request
timeout, per account join interval, per account join cap, concurrency and the
total.  A dry run prints every join it would perform, in the order it would
perform it, with the account that would perform it.
"""

from __future__ import annotations

import random
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
         "seconds before a request is called a timeout (validated, shown in "
         "the plan; Telethon has no per-request timeout to wire it to)"),
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
    Flag("--mute-channels", "mute_channels", None, "mute joined channels",
         action="store_true"),
    Flag("--mute-private", "mute_private", None, "mute joined private chats",
         action="store_true"),
    Flag("--mute-interval", "mute_interval", float, "gap between mutes, seconds"),
    Flag("--leave-interval", "leave_interval", float, "gap between leaves, seconds"),
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
    #: ``muteChannels`` / ``mutePrivate`` and the two gaps. ``leaveMode`` has
    #: no CLI flag: leaving is chosen by ``operation_type``, and how a leave
    #: behaves is decided per chat by the sender (LeaveChannelRequest for a
    #: channel, DeleteChatUserRequest for a plain group) - a mode string would
    #: only restate that.
    mute_channels: bool = False
    mute_private: bool = False
    mute_interval: float = 0.0
    leave_interval: float = 0.0
    #: The dump's ``autoDownload*`` / ``autoPlay*`` toggles configure the
    #: reference client's media autoload preferences - a GUI concept this CLI
    #: has no counterpart for, and the join page never downloaded media. The
    #: nine flags were display-only and are gone.
    task_name: str = "batch-join"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> JoinConfig:
        del settings
        data = section(raw, SECTION)
        config = cls(
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
            mute_channels=bool(data.get("mute_channels", False)),
            mute_private=bool(data.get("mute_private", False)),
            mute_interval=_seconds(data, "mute_interval", 0.0),
            leave_interval=_seconds(data, "leave_interval", 0.0),
            task_name=name_of(raw, data, "batch-join"),
        )
        config.validate()
        return config

    def validate(self) -> None:
        """The page's own configuration errors, in the page's own words."""
        if self.per_account_interval_max and \
                self.per_account_interval_max < self.per_account_interval_min:
            raise ConfigError(
                f"{SECTION}: per-account join interval max must be >= min")
        if 0 < self.per_account_interval_min < 1:
            raise ConfigError(
                f"{SECTION}: per-account join interval must be at least "
                f"1 second")
        if 0 < self.request_timeout < 1:
            raise ConfigError(
                f"{SECTION}: request timeout must be at least 1 second")


def every_account(accounts: list[str], targets: list[Target],
                  per_account_cap: int, exec_cap: int,
                  max_join_count: int = 0) -> list[tuple[str, Target]]:
    """``Each account joins all groups``: the cross product, not a rotation."""
    pairs: list[tuple[str, Target]] = []
    for account in accounts:
        for used, target in enumerate(targets):
            if per_account_cap and used >= per_account_cap:
                break
            if exec_cap and len(pairs) >= exec_cap:
                return pairs
            if max_join_count and len(pairs) >= max_join_count:
                return pairs
            pairs.append((account, target))
    return pairs


def assign(accounts: list[str], targets: list[Target],
           per_account_cap: int, exec_cap: int,
           max_join_count: int = 0) -> list[tuple[str, Target]]:
    """Round robin, honouring the per account cap and the overall one.

    ``maxJoinCount`` is the page's own ceiling on how many chats one run may
    join at all: with it set, the assignment stops there rather than trimming
    afterwards, so the header's queue count is the number that gets tried.
    """
    pairs: list[tuple[str, Target]] = []
    if not accounts or not targets:
        return pairs
    used: dict[str, int] = {account: 0 for account in accounts}
    for position, target in enumerate(targets):
        if max_join_count and len(pairs) >= max_join_count:
            break
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
        if config.per_account_interval_max:
            # ``perAccountJoinIntervalMin/Max``: a range, not a number - the
            # account's own gap is drawn from it so a pool does not move in
            # lockstep.
            wait = max(wait, random.uniform(config.per_account_interval_min,
                                            config.per_account_interval_max))
        else:
            wait = max(wait, config.account_interval)
    return wait


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
    mode = config.join_mode.strip().lower().replace("-", "_").replace(" ", "_")
    all_groups = mode in ("all", "each", "each_account_all_groups",
                          "all_groups", "every")
    build = every_account if all_groups else assign
    pairs = build(labels, targets, config.per_account_cap,
                  config.exec_cap, config.max_join_count) if labels else []
    if all_groups and len(pairs) > 1:
        print(f"join mode: each account joins all groups ({len(pairs)} "
              f"join(s))")
    if config.max_join_count and len(targets) > config.max_join_count:
        print(f"max_join_count: {config.max_join_count} of "
              f"{len(targets)} target(s) planned")
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
        total=len(pairs),
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"targets": [t.ref for t in config.targets]}, len(pairs))
    sender = ctx.sender()

    if config.max_joined_chats and ctx.live and labels:
        # ``maxJoinedChatsPerAccount``: a full account does not take more chats.
        full = set()
        for label in labels:
            counted = sender.chat_count(label)
            if not counted.ok:
                print(f"chat count {label} -> {counted.code} (cap not applied)")
                continue
            if counted.count >= config.max_joined_chats:
                full.add(label)
                print(f"account full: {label} already in {counted.count} "
                      f"chat(s) (max_joined_chats={config.max_joined_chats})")
        kept = [pair for pair in pairs if pair[0] not in full]
        for account, target in pairs:
            if account in full:
                record(ctx, task, account, target.ref, False, "ACCOUNT_FULL")
                print(f"skipped {target.ref} for {account} (account full)")
        pairs = kept
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
