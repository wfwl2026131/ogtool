"""#17 batch invite v2 - invite your own contacts into groups you own.

The page is the second generation of #11: the source is the account's own
contact list instead of a member file, and the exports are the PEER_FLOOD and
TIMEOUT records.  Fields: target, minimum request interval, threads, per target
success cap, success cap, execution cap, invite interval range, the two
PEER_FLOOD caps and the export path.
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ConfigError, Settings
from ..flags import Flag
from ..limiter import PeerFloodBackoff, format_duration
from ..report import export
from ..runtime import RunContext
from ..targets import parse_many
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
    read_list_file,
    record,
    section,
    targets_of,
    text,
)

FEATURE = "batch_invite_v2"
SECTION = "batch_invite_v2"
TITLE = "#17 批量拉人v2"

FLAGS = (
    Flag("--targets", "targets", str, "own groups/channels, comma separated"),
    Flag(
        "--targets-file", "files", str, "file with one target per line", action="append"
    ),
    Flag("--contacts", "contacts", str, "contacts, comma separated"),
    Flag(
        "--contacts-file",
        "contact_files",
        str,
        "file with one contact per line",
        action="append",
    ),
    Flag(
        "--request-interval",
        "request_interval",
        float,
        "minimum seconds between two requests",
    ),
    Flag(
        "--threads",
        "threads",
        int,
        "one lane per handler account, lanes run at the same time",
    ),
    Flag(
        "--target-success-cap", "target_success_cap", int, "invites one target accepts"
    ),
    Flag("--success-cap", "success_cap", int, "total successes"),
    Flag("--exec-cap", "exec_cap", int, "total attempts"),
    Flag(
        "--member-file",
        "member_file",
        str,
        "file with one member per line (memberInputMode=file)",
    ),
    Flag("--member-input-mode", "member_input_mode", str, "manual, text or file"),
    Flag("--wait-minutes", "wait_minutes", float, "pause between two targets, minutes"),
    Flag(
        "--limit-count-type",
        "limit_count_type",
        str,
        "success or exec: what the per-target cap counts",
    ),
    Flag(
        "--invites-per-request",
        "invites_per_request",
        int,
        "how many invites share one request gap",
    ),
    Flag(
        "--peer-flood-target-max-retries",
        "peer_flood_target_max_retries",
        int,
        "PEER_FLOOD retries for one target",
    ),
    Flag(
        "--custom-task-key", "custom_task_key", str, "task key written into the export"
    ),
    Flag(
        "--task-mark/--no-task-mark",
        "task_mark_enabled",
        None,
        "write the task key into the export (default: on)",
        action="bool_pair",
    ),
    Flag(
        "--continue-members/--no-continue-members",
        "continue_members_across_targets",
        None,
        "retry a contact on the next target after it failed on one",
        action="bool_pair",
    ),
    Flag(
        "--invite-interval-min", "invite_interval_min", float, "minimum invite interval"
    ),
    Flag(
        "--invite-interval-max", "invite_interval_max", float, "maximum invite interval"
    ),
    Flag("--flood-cap", "flood_cap", int, "PEER_FLOOD retries per handler"),
    Flag(
        "--invitee-flood-cap",
        "invitee_flood_cap",
        int,
        "PEER_FLOOD retries per invitee",
    ),
    Flag(
        "--output-dir",
        "output_dir",
        str,
        "directory for exports (default: exports under logs)",
    ),
    Flag(
        "--resume/--no-resume",
        "resume",
        None,
        "skip (handler, group<-contact) triples already invited",
    ),
    Flag(
        "--monitor-group",
        "monitor_group",
        str,
        "watch this group and invite members as they join",
    ),
    Flag(
        "--monitor-seconds",
        "monitor_seconds",
        int,
        "stop watching after this many seconds (0 = until interrupted)",
    ),
    Flag(
        "--monitor-invite-all/--no-monitor-invite-all",
        "monitor_skip_existing",
        None,
        "also invite existing members at start (default: only new joins)",
    ),
    Flag(
        "--export-flood-timeout",
        "export_flood_timeout",
        str,
        "where the PEER_FLOOD and TIMEOUT record goes",
    ),
)


def _file_lines(data: dict, key: str) -> list[str]:
    files = data.get(key) or []
    if isinstance(files, str):
        files = [files]
    out: list[str] = []
    for path in files:
        out.extend(read_list_file(str(path)))
    return out


@dataclass(slots=True)
class InviteV2Config:
    account_labels: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    contacts: list[str] = field(default_factory=list)
    request_interval: float = 8.0
    threads: int = 1
    target_success_cap: int = 0
    success_cap: int = 0
    exec_cap: int = 0
    invite_interval_min: float = 8.0
    invite_interval_max: float = 15.0
    flood_cap: int = 3
    invitee_flood_cap: int = 2
    export_flood_timeout: str = ""
    output_dir: str = ""
    # --- the page's config, by its own names ---------------------------
    #: ``memberInputMode`` / ``memberFilePath``: 手动 / 文本 / 文件 三选一。
    member_input_mode: str = "manual"
    member_file: str = ""
    #: ``continueMembersAcrossTargets``: 一个联系人被某个群拒了，还要不要
    #: 在下一个群再试一次。关掉 = 失败即全放弃。
    continue_members_across_targets: bool = True
    #: ``waitMinutes``: 两个目标之间的停顿，分钟。
    wait_minutes: float = 0.0
    #: ``limitCountType``: 上限按成功数还是按执行数算。
    limit_count_type: str = "success"
    #: ``customTaskKey`` / ``taskMarkEnabled``: 任务标记，写进结果导出。
    custom_task_key_enabled: bool = False
    custom_task_key: str = ""
    task_mark_enabled: bool = True
    #: ``invitesPerRequest``: 一次请求带几个人。
    invites_per_request: int = 1
    #: ``peerFloodTargetMaxRetries``: 某个群 PEER_FLOOD 时的重试封顶。
    peer_flood_target_max_retries: int = 3
    #: 监视模式 (MonitorInviteStrategy): 不吃静态名单, 蹲在 --monitor-group
    #: 里等新人进群, 进一个邀一个, 直到 --monitor-seconds 用完 (0=直到打断)。
    #: 断点续跑: results 表里该 feature 已 sent 的 (handler, group<-contact)。
    resume: bool = False
    monitor_group: str = ""
    monitor_seconds: int = 0
    monitor_skip_existing: bool = True
    task_name: str = "batch-invite-v2"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> InviteV2Config:
        del settings
        data = section(raw, SECTION)
        targets = [t.ref for t in targets_of(data, kind="group")]
        raw_contacts = data.get("contacts")
        if isinstance(raw_contacts, str):
            contacts = [
                part.strip()
                for part in raw_contacts.replace("\n", ",").split(",")
                if part.strip()
            ]
        elif isinstance(raw_contacts, (list, tuple)):
            contacts = [str(item) for item in raw_contacts]
        else:
            contacts = []
        contacts.extend(_file_lines(data, "contact_files"))
        monitor_group = text(data, "monitor_group")
        if not targets and not monitor_group:
            raise ConfigError(f"{SECTION}: no target group given")
        mode = str(data.get("member_input_mode") or "manual").lower()
        if mode not in ("manual", "text", "file"):
            raise ConfigError(
                f"{SECTION}: member_input_mode must be manual, text or file, is {mode}"
            )
        if mode == "file":
            file_name = text(data, "member_file")
            if not file_name:
                raise ConfigError(
                    f"{SECTION}: member_input_mode=file needs member_file"
                )
            contacts.extend(_file_lines(data, "member_file"))
        limit_type = str(data.get("limit_count_type") or "success").lower()
        if limit_type not in ("success", "exec"):
            raise ConfigError(
                f"{SECTION}: limit_count_type must be success or exec, is {limit_type}"
            )
        # :1743427 names the same switch the reference client uses to skip
        # the targets the receipts already cover; ``resume`` is our spelling.
        resume = bool(data.get("resume", False)) or bool(
            data.get("skipProcessedTargets", False)
        )
        return cls(
            account_labels=accounts_of(raw, data),
            targets=targets,
            contacts=_unique([str(item) for item in contacts]),
            request_interval=num(data, "request_interval", 8.0),
            threads=num(data, "threads", 1, kind=int, minimum=1),
            target_success_cap=num(data, "target_success_cap", 0, kind=int),
            success_cap=num(data, "success_cap", 0, kind=int),
            exec_cap=num(data, "exec_cap", 0, kind=int),
            invite_interval_min=num(data, "invite_interval_min", 8.0),
            invite_interval_max=num(data, "invite_interval_max", 15.0),
            flood_cap=num(data, "flood_cap", 3, kind=int),
            invitee_flood_cap=num(data, "invitee_flood_cap", 2, kind=int),
            export_flood_timeout=text(data, "export_flood_timeout"),
            output_dir=text(data, "output_dir"),
            member_input_mode=mode,
            member_file=text(data, "member_file"),
            continue_members_across_targets=bool(
                data.get("continue_members_across_targets", True)
            ),
            wait_minutes=num(data, "wait_minutes", 0.0, minimum=0.0),
            limit_count_type=limit_type,
            custom_task_key_enabled=bool(data.get("custom_task_key_enabled")),
            custom_task_key=text(data, "custom_task_key"),
            task_mark_enabled=bool(data.get("task_mark_enabled", True)),
            invites_per_request=num(
                data, "invites_per_request", 1, kind=int, minimum=1
            ),
            resume=resume,
            monitor_group=text(data, "monitor_group"),
            monitor_seconds=num(data, "monitor_seconds", 0, kind=int),
            monitor_skip_existing=bool(data.get("monitor_skip_existing", True)),
            peer_flood_target_max_retries=num(
                data, "peer_flood_target_max_retries", 3, kind=int, minimum=0
            ),
            task_name=name_of(raw, data, "batch-invite-v2"),
        )


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text_value = item.strip()
        if text_value and text_value not in seen:
            seen.add(text_value)
            out.append(text_value)
    return out


def build_jobs(
    targets: list[str],
    contacts: list[str],
    handlers: list[str],
    *,
    target_success_cap: int = 0,
    exec_cap: int = 0,
    cap_by: str = "success",
) -> list[tuple[str, str, str]]:
    """Round robin handlers over contacts, one target at a time.

    ``cap_by`` is ``limitCountType``: the per-target number counts successes
    (the default) or attempts. Counting attempts means a target that keeps
    failing still consumes its cap, which is what "exec" says on the page.
    """
    jobs: list[tuple[str, str, str]] = []
    if not handlers or not contacts:
        return jobs
    per_target = exec_cap if cap_by == "exec" and exec_cap else target_success_cap
    for target in targets:
        for taken, member in enumerate(contacts, start=1):
            if per_target and taken > per_target:
                break
            if exec_cap and len(jobs) >= exec_cap:
                return jobs
            handler = handlers[len(jobs) % len(handlers)]
            jobs.append((handler, target, member))
    return jobs


def gap(config: InviteV2Config, rng: random.Random | None = None) -> float:
    """Seconds between two invites: the request gap, or the invite range.

    The page shows an invite interval *range*; the sample is drawn per gap so
    a long run does not settle into one detectable rhythm.
    """
    generator = rng or random.Random()
    return max(
        config.request_interval,
        generator.uniform(config.invite_interval_min, config.invite_interval_max),
    )


@dataclass(slots=True)
class LaneStat:
    """What one handler account managed inside a lane."""

    ok: int = 0
    failed: int = 0
    targets: set[str] = field(default_factory=set)


@dataclass(slots=True)
class LaneResult:
    """One lane's tally, its export rows and the reason it stopped.

    The rows travel back as data instead of hanging off the task: a task
    receipt is a slots dataclass, so a lane that stashed them there would
    raise ``AttributeError`` on its first success.
    """

    ok: int = 0
    failed: int = 0
    flood: list[str] = field(default_factory=list)
    timeouts: list[str] = field(default_factory=list)
    success_rows: list[str] = field(default_factory=list)
    failed_rows: list[str] = field(default_factory=list)
    stats: dict[str, LaneStat] = field(default_factory=dict)
    stop_reason: str = "targets"

    def merge(self, other: LaneResult) -> None:
        """Fold another lane's numbers into this one."""
        self.ok += other.ok
        self.failed += other.failed
        self.flood.extend(other.flood)
        self.timeouts.extend(other.timeouts)
        self.success_rows.extend(other.success_rows)
        self.failed_rows.extend(other.failed_rows)
        for account, stat in other.stats.items():
            mine = self.stats.setdefault(account, LaneStat())
            mine.ok += stat.ok
            mine.failed += stat.failed
            mine.targets |= stat.targets


def _invite_lane(
    sender: object,
    ctx: RunContext,
    task: object,
    lane: list[tuple[str, str, str]],
    total: int,
    config: InviteV2Config,
    backoff: PeerFloodBackoff,
    waited: list[float],
) -> LaneResult:
    """One lane: its own handler accounts, its own pace, its own tallies.

    The result ends with the lane's stop reason - ``limit`` when a cap was
    reached, ``inviters`` when every handler rested on PEER_FLOOD,
    ``targets`` when the lane simply ran out - which feeds the reference's
    three completion sentences (:1743436-1743442).
    """
    result = LaneResult()
    rng = random.Random(len(lane))
    abandoned: set[str] = set()
    # ``peerFloodTargetMaxRetries``: a *target's* own retry ceiling, separate
    # from the per-invitee cap. Once a group has answered PEER_FLOOD that many
    # times it is rested, not hammered for every remaining contact in it.
    target_floods: dict[str, int] = {}
    exhausted: set[str] = set()
    # ``floodCap`` is the handler's own patience: after that many PEER_FLOOD
    # answers this handler stops for the rest of the lane, which is a different
    # number from ``inviteeFloodCap`` (retries for one contact).
    handler_floods: dict[str, int] = {}
    rested: set[str] = set()
    # The per-group success counter behind :1743447 "Target %1 reached
    # per-group success limit (%2/%3)."
    target_ok: dict[str, int] = {}
    capped_targets: set[str] = set()
    # :1743438 is a per-target sentence: once a group is capped the message
    # is printed once for that group, not once per skipped member.
    capped_reported: set[str] = set()
    previous_target = ""
    for index, (handler, group, contact) in enumerate(lane, start=1):
        if config.success_cap and result.ok >= config.success_cap:
            print(f"stopped: success_cap ({config.success_cap})")
            result.stop_reason = "limit"
            break
        if config.exec_cap and result.ok + result.failed >= config.exec_cap:
            print(f"stopped: exec_cap ({config.exec_cap})")
            result.stop_reason = "limit"
            break
        if group in capped_targets:
            record(ctx, task, handler, f"{group}<-{contact}", False, "TARGET_CAPPED")
            if group not in capped_reported:
                capped_reported.add(group)
                print(
                    f"All available worker accounts reached their limit for "
                    f"{group}, skipping remaining members."
                )
            continue
        if handler in rested:
            record(ctx, task, handler, f"{group}<-{contact}", False, "HANDLER_REST")
            print(
                f"[{index:03d}/{total:03d}] skipped {contact} in {group} "
                f"(handler rested after {handler_floods.get(handler, 0)} "
                f"PEER_FLOOD)"
            )
            continue
        if group in exhausted:
            record(ctx, task, handler, f"{group}<-{contact}", False, "TARGET_REST")
            print(
                f"[{index:03d}/{total:03d}] skipped {contact} in {group} "
                f"(target rested after "
                f"{target_floods.get(group, 0)} PEER_FLOOD)"
            )
            continue
        if not config.continue_members_across_targets and contact in abandoned:
            print(
                f"[{index:03d}/{total:03d}] skipped {contact} "
                f"(already failed on another target)"
            )
            continue
        if index > 1:
            if group != previous_target and config.wait_minutes:
                # waitMinutes is a pause between two targets, not between two
                # invites: it is the page's "let the last group settle" knob.
                rest = config.wait_minutes * 60.0
                waited.append(rest)
                pause(ctx, rest)
            elif (
                index % max(1, config.invites_per_request) == 1
                or config.invites_per_request == 1
            ):
                delay = gap(config, rng)
                waited.append(delay)
                pause(ctx, delay)
        previous_target = group
        target = parse_many([group], "group")[0]
        member = parse_many([contact], "custom")[0]
        # The account only enters the statistics once it really invites:
        # a handler that rested or a capped target never counts as a target.
        stat = result.stats.setdefault(handler, LaneStat())
        stat.targets.add(group)
        floods = 0
        while True:
            answer = sender.invite(handler, target, member)  # type: ignore[attr-defined]
            if answer.code != "PEER_FLOOD" or floods >= config.invitee_flood_cap:
                break
            floods += 1
            wait = backoff.wait_for(floods - 1)
            print(
                f"  PEER_FLOOD backoff {wait:.0f}s "
                f"(attempt {floods}/{config.invitee_flood_cap}) {contact}"
            )
            if ctx.live:
                time.sleep(wait)
        if answer.ok:
            result.ok += 1
            stat.ok += 1
            record(ctx, task, handler, f"{group}<-{contact}", True)
            result.success_rows.append(f"#{handler} {contact} -> {group} (success)")
            print(f"[InviteSuccess] {contact} Account #{handler} {group} -> ok")
            print(
                f"✅ SENT [{index:03d}/{total:03d}] handler={handler} "
                f"target={group} contact={contact} -> ok"
            )
            if config.target_success_cap:
                target_ok[group] = target_ok.get(group, 0) + 1
                if target_ok[group] >= config.target_success_cap:
                    capped_targets.add(group)
                    # :1743441 is printed the moment the counter reaches the
                    # cap, not later when the group is skipped.
                    print(
                        f"Target {group} reached per-group success limit "
                        f"({target_ok[group]}/{config.target_success_cap})."
                    )
            continue
        result.failed += 1
        stat.failed += 1
        record(
            ctx, task, handler, f"{group}<-{contact}", False, answer.code, answer.detail
        )
        result.failed_rows.append(
            f"#{handler} {contact} -> {group} (failed: {answer.code})"
        )
        # :1743445 is the failure twin of the :1743446 success row: the same
        # shape, printed live and kept in the export file.
        print(f"#{handler} {contact} -> {group} (failed: {answer.code})")
        print(
            f"[{index:03d}/{total:03d}] handler={handler} "
            f"target={group} contact={contact} -> {answer.code}"
        )
        if not config.continue_members_across_targets:
            abandoned.add(contact)
        if answer.code == "PEER_FLOOD":
            handler_floods[handler] = handler_floods.get(handler, 0) + 1
            if config.flood_cap and handler_floods[handler] >= config.flood_cap:
                rested.add(handler)
                print(
                    f"handler rested: {handler} answered PEER_FLOOD "
                    f"{handler_floods[handler]} time(s) "
                    f"(flood_cap={config.flood_cap})"
                )
            target_floods[group] = target_floods.get(group, 0) + 1
            cap = config.peer_flood_target_max_retries
            if cap and target_floods[group] >= cap:
                exhausted.add(group)
                print(
                    f"target rested: {group} answered PEER_FLOOD "
                    f"{target_floods[group]} time(s) "
                    f"(peer_flood_target_max_retries={cap})"
                )
            if contact not in result.flood:
                result.flood.append(contact)
        if answer.code == "TIMEOUT" and contact not in result.timeouts:
            result.timeouts.append(contact)
    if rested and not (set(h for h, _, _ in lane) - rested):
        result.stop_reason = "inviters"
    return result


def _monitor(config: InviteV2Config, ctx: RunContext) -> int:
    """MonitorInviteStrategy: watch ``monitor_group``, invite joiners.

    Polling, not events: ``events.ChatAction`` needs a live telethon client
    per watcher account, and the simulator has to be able to drive this
    path in tests. A poll every ``request_interval`` seconds reads the
    latest participants and invites the ones the cursor has not seen; the
    cursor (last seen user id per target) rides the task receipt so a
    restart resumes instead of re-inviting.
    """

    from ogtool.targets import parse_many

    accounts = ctx.bind(config.account_labels)
    handlers = [account.label for account in accounts]
    if not handlers:
        print("ERROR: monitor mode needs at least one bound account")
        return 2
    watch = parse_many([config.monitor_group], "group")[0]
    targets = config.targets or [config.monitor_group]
    seen: set[str] = set()
    sender = ctx.sender()
    started = time.monotonic()
    ok = failed = rounds = 0
    print(f"task: {config.task_name} (monitor)")
    print(f"watch: {watch.ref}  invite to: {', '.join(targets)}")
    print(
        f"accounts: {', '.join(handlers)}  interval: "
        f"{max(config.request_interval, config.invite_interval_min):.0f}s"
    )
    print(
        "monitor: waiting for members to join "
        + (
            "(Ctrl+C to stop)"
            if not config.monitor_seconds
            else f"(up to {config.monitor_seconds}s)"
        )
    )
    try:
        while (
            not config.monitor_seconds
            or time.monotonic() - started < config.monitor_seconds
        ):
            rounds += 1
            try:
                result = sender.get_participants(  # type: ignore[attr-defined]
                    handlers[0], watch, limit=0
                )
            except AttributeError:
                print("ERROR: the sender cannot list members")
                return 2
            if not result.ok:
                failed += 1
                print(f"❌ FAIL monitor poll {result.code}")
                pause(ctx, max(config.request_interval, 2.0))
                continue
            users = list(getattr(result, "items", []) or [])
            fresh = [u for u in users if u not in seen]
            if config.monitor_skip_existing and rounds == 1:
                seen.update(users)
                print(f"monitor: {len(users)} existing member(s) baseline")
                fresh = []
            for user in fresh:
                seen.add(user)
                for target in targets:
                    group = parse_many([target], "group")[0]
                    member = parse_many([user], "custom")[0]
                    handler = handlers[ok % len(handlers)]
                    pause(ctx, gap(config))
                    invite = sender.invite(handler, group, member)
                    if invite.ok:
                        ok += 1
                        print(
                            f"✅ SENT [{ok:03d}] handler={handler} "
                            f"target={target} contact={user} -> ok"
                        )
                    else:
                        failed += 1
                        print(
                            f"❌ FAIL [{failed:03d}] handler={handler} "
                            f"target={target} contact={user} "
                            f"code={invite.code}"
                        )
            if fresh or rounds == 1:
                print(
                    f"monitor: round {rounds} - {len(fresh)} new "
                    f"member(s), invited {ok}, failed {failed}"
                )
            pause(ctx, max(config.request_interval, 2.0))
    except KeyboardInterrupt:  # pragma: no cover - operator stops it
        print("monitor: interrupted by the operator")
        return 130 if not ok else 0
    print(
        f"MONITOR: watched {watch.ref} for {config.monitor_seconds}s, "
        f"invited {ok}, failed {failed}"
    )
    return 0 if not failed else 1


def run(config: InviteV2Config, ctx: RunContext) -> int:
    if config.monitor_group:
        return _monitor(config, ctx)
    accounts = ctx.bind(config.account_labels)
    handlers = [account.label for account in accounts] or ["-"]
    jobs = build_jobs(
        config.targets,
        config.contacts,
        handlers,
        target_success_cap=config.target_success_cap,
        exec_cap=config.exec_cap,
        cap_by=config.limit_count_type,
    )
    skipped_pairs: list[tuple[str, str]] = []
    if config.resume:
        done = ctx.store.sent_pairs(FEATURE)
        before = len(jobs)
        keep: list[tuple[str, str, str]] = []
        for handler, group, contact in jobs:
            if (handler, f"{group}<-{contact}") in done:
                if (handler, group) not in skipped_pairs:
                    skipped_pairs.append((handler, group))
                continue
            keep.append((handler, group, contact))
        jobs = keep
        skipped = before - len(jobs)
        if skipped:
            print(
                f"RESUME: skipping {skipped} already-invited "
                f"contact(s); {len(jobs)} left"
            )
            # :1743431 - one line per (account, target) the receipts already
            # cover, the account it was done for and the receipt's result.
            for handler, group in skipped_pairs:
                print(f"Skip processed target: {handler} -> {group} (result: sent)")

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
        + (" (no account bound, plan only)" if not accounts else ""),
        targets=len(config.targets),
        contacts=len(config.contacts),
        request_interval=f"{config.request_interval:.0f}s",
        threads=config.threads,
        target_success_cap=config.target_success_cap or "-",
        success_cap=config.success_cap or "-",
        exec_cap=config.exec_cap or "-",
        invite_interval=f"{config.invite_interval_min:.0f}-"
        f"{config.invite_interval_max:.0f}s",
        flood_cap=config.flood_cap,
        invitee_flood_cap=config.invitee_flood_cap,
        member_input=f"{config.member_input_mode}"
        + (f" ({config.member_file})" if config.member_file else ""),
        limit_count_type=config.limit_count_type,
        wait_minutes=config.wait_minutes or "-",
        invites_per_request=config.invites_per_request,
        task_key=config.custom_task_key or "-",
        queue=len(jobs),
    )
    # The dump's ``maxInviteIntervalMs`` is superseded by the invite interval
    # range (inviteIntervalMin/MaxSec): the page already samples the gap per
    # invite, so a hard millisecond cap would only clip the drawn range.

    # The plan, per account: how many groups it owns and how many contacts it
    # will invite (:1743428-1743429).  An account without a single contact is
    # skipped with its own sentence.
    plan_targets: dict[str, set[str]] = {}
    plan_contacts: dict[str, set[str]] = {}
    for handler, group, contact in jobs:
        plan_targets.setdefault(handler, set()).add(group)
        plan_contacts.setdefault(handler, set()).add(contact)
    for account in accounts:
        label = account.label
        own_targets = len(plan_targets.get(label, ()))
        own_contacts = len(plan_contacts.get(label, ()))
        if not own_contacts:
            print(f"Account #{label} skipped: no contacts")
        else:
            print(f"Account #{label}: {own_targets} targets, {own_contacts} contacts")

    task = open_task(
        ctx,
        config.task_name,
        FEATURE,
        {"targets": config.targets, "contacts": config.contacts},
        len(jobs),
    )
    sender = ctx.sender()

    backoff = PeerFloodBackoff()
    waited: list[float] = []
    tally = LaneResult()
    lane_results: list[LaneResult] = []
    if not jobs:
        # An empty plan is a legitimate answer (nothing matched the filters), not
        # a crash: there is simply no lane to build.
        if not config.targets and not config.contacts:
            print("No members or targets configured")
        elif not config.contacts:
            print("Empty members")
        if skipped_pairs:
            print("All targets already processed (result: sent)")
        else:
            print("No executable accounts found (no owned targets or no contacts)")
        print("no jobs: nothing to invite (check the target and member lists)")
        print("PLAN: 0 invites, failed 0")
        close_task(ctx, task, ok=0, failed=0)
        return 0
    lanes = lanes_by(lambda job: job[0], jobs, config.threads)
    if len(lanes) > 1:
        print(f"threads: {len(lanes)} lanes, one handler account each")
        with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
            work = [
                pool.submit(
                    _invite_lane,
                    sender,
                    ctx,
                    task,
                    lane,
                    len(jobs),
                    config,
                    backoff,
                    waited,
                )
                for lane in lanes
            ]
            for job in work:
                lane_result = job.result()
                tally.merge(lane_result)
                lane_results.append(lane_result)
    else:
        lane_result = _invite_lane(
            sender, ctx, task, lanes[0], len(jobs), config, backoff, waited
        )
        tally.merge(lane_result)
        lane_results.append(lane_result)

    ok = tally.ok
    failed = tally.failed
    flood = tally.flood
    timeouts = tally.timeouts
    print(
        f"ok: {ok}  failed: {failed}  PEER_FLOOD: {len(flood)}  "
        f"TIMEOUT: {len(timeouts)}"
    )
    if ctx.live and waited:
        print(f"waited: {format_duration(sum(waited))}")
    print(f"PLAN: {len(jobs)} invites, {len(handlers)} handlers, exec {ok + failed}")
    processed = ok + failed
    print(f"Processed: {processed}/{len(jobs)}")
    # :1743447 - one line per account: what it managed and how many groups it
    # touched.  The reference's "%4 %5" reads as a number plus a unit word,
    # so the unit is the count of distinct targets.
    for account in accounts:
        stat = tally.stats.get(account.label) or LaneStat()
        print(
            f"#{account.label} Batch invite {stat.ok} success, "
            f"{stat.failed} failed -> {len(stat.targets)} targets"
        )
    # The three completion sentences (:1743436-1743442): "inviters" only when
    # EVERY lane says so, "limit" as soon as one lane hit a cap.
    reasons = [lane_result.stop_reason for lane_result in lane_results]
    if reasons and all(reason == "inviters" for reason in reasons):
        completed = "All inviter accounts exhausted"
    elif any(reason == "limit" for reason in reasons):
        completed = "All accounts reached limit"
    else:
        completed = "All targets processed"
    if ctx.live:
        print(
            f"Completed task {task.id}: {completed}, "
            f"processed {processed}, success {ok}, failed {failed}"
        )
        print(f"Batch Invite Task {task.id} Completed.")

    base = Path(out_dir(ctx, {"output_dir": config.output_dir}, ctx.settings))
    path = config.export_flood_timeout or f"{base}/batch_invite_v2_flood.txt"
    # A key that was typed is meant to be used; the page's separate "enabled"
    # switch only matters when a key comes from a stored config.
    mark = (
        config.custom_task_key
        if config.task_mark_enabled
        and (config.custom_task_key or config.custom_task_key_enabled)
        else ""
    )
    rows = [f"PEER_FLOOD\t{item}\t{mark}" for item in flood]
    rows += [f"TIMEOUT\t{item}\t{mark}" for item in timeouts]
    export(path, rows, "PEER_FLOOD and TIMEOUT")
    _write_batch_report(
        config,
        base,
        task,
        handlers,
        len(config.targets),
        len(config.contacts),
        len(jobs),
        ok,
        failed,
        tally.success_rows,
        tally.failed_rows,
    )
    close_task(ctx, task, ok=ok, failed=failed)
    return 0 if not failed else 1


def _write_batch_report(
    config: InviteV2Config,
    base: Path,
    task: object,
    handlers: list[str],
    targets: int,
    members: int,
    total: int,
    ok: int,
    failed: int,
    success_rows: list[str] | None = None,
    failed_rows: list[str] | None = None,
) -> None:
    """The page's own report trio, when an output directory is set.

    ``success_invites_<name>_task_<id>.txt`` and ``failed_invites_...txt``
    list the invites that landed and the ones that did not, saved with the
    page's own sentences (:1743398-1743401); ``invite_report_...txt`` carries
    the statistics and the configuration sections the page shows
    (:1743404-1743421), with the interval fields in the units the page names
    and the closing sentence (:1743423).
    """
    from datetime import datetime

    success_lines = list(success_rows or [])
    failed_lines = list(failed_rows or [])
    report = [
        "=== Batch Invite Report ===",
        f"Task ID: {getattr(task, 'id', '-')}",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "=== Statistics ===",
        f"Target Groups: {targets}",
        f"Members to Invite: {members}",
        f"Total Invitations: {total}",
        f"Processed: {ok + failed}",
        f"Successful: {ok}",
        f"Failed: {failed}",
        "=== Configuration ===",
        f"Worker Accounts: {', '.join(handlers) or '-'}",
        f"Members Source: {config.member_input_mode}"
        + (f" ({config.member_file})" if config.member_file else ""),
        f"Members File: {config.member_file or '-'}",
        f"Batch Size: {config.invites_per_request}",
        f"Interval: {int(config.wait_minutes * 60000)} millsseconds",
        f"Request Interval: {int(config.request_interval * 1000)} milliseconds",
        f"Invite Interval Range: "
        f"{int(config.invite_interval_min * 1000)}-"
        f"{int(config.invite_interval_max * 1000)}",
    ]
    if config.target_success_cap:
        report.append(f"Target per-group success limit: {config.target_success_cap}")
    base.mkdir(parents=True, exist_ok=True)
    task_id = getattr(task, "id", 0)
    # The rows start with "#" (the account), so they are written raw: a
    # comment-aware writer would drop every single one of them.
    success_path = base / f"success_invites_{config.task_name}_task_{task_id}.txt"
    success_path.write_text(
        "\n".join(success_lines) + ("\n" if success_lines else ""), encoding="utf-8"
    )
    print(f"Saved {len(success_lines)} successful invites to {success_path}")
    failed_path = base / f"failed_invites_{config.task_name}_task_{task_id}.txt"
    failed_path.write_text(
        "\n".join(failed_lines) + ("\n" if failed_lines else ""), encoding="utf-8"
    )
    print(f"Saved {len(failed_lines)} failed invites to {failed_path}")
    report_path = base / f"invite_report_{config.task_name}_task_{task_id}.txt"
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"Generated report: {report_path}")


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(InviteV2Config.from_dict(raw, ctx.settings), ctx)


__all__ = [
    "InviteV2Config",
    "LaneResult",
    "LaneStat",
    "TITLE",
    "_monitor",
    "build_jobs",
    "gap",
    "main",
    "run",
]
