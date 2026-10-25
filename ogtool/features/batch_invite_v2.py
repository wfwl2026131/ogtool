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
    Flag("--targets-file", "files", str, "file with one target per line",
         action="append"),
    Flag("--contacts", "contacts", str, "contacts, comma separated"),
    Flag("--contacts-file", "contact_files", str,
         "file with one contact per line", action="append"),
    Flag("--request-interval", "request_interval", float,
         "minimum seconds between two requests"),
    Flag("--threads", "threads", int,
         "one lane per handler account, lanes run at the same time"),
    Flag("--target-success-cap", "target_success_cap", int,
         "invites one target accepts"),
    Flag("--success-cap", "success_cap", int, "total successes"),
    Flag("--exec-cap", "exec_cap", int, "total attempts"),
    Flag("--member-file", "member_file", str,
         "file with one member per line (memberInputMode=file)"),
    Flag("--member-input-mode", "member_input_mode", str,
         "manual, text or file"),
    Flag("--wait-minutes", "wait_minutes", float,
         "pause between two targets, minutes"),
    Flag("--limit-count-type", "limit_count_type", str,
         "success or exec: what the per-target cap counts"),
    Flag("--invites-per-request", "invites_per_request", int,
         "how many invites share one request gap"),
    Flag("--max-invite-interval-ms", "max_invite_interval_ms", int,
         "upper bound of the invite interval, milliseconds"),
    Flag("--peer-flood-target-max-retries", "peer_flood_target_max_retries",
         int, "PEER_FLOOD retries for one target"),
    Flag("--custom-task-key", "custom_task_key", str,
         "task key written into the export"),
    Flag("--task-mark/--no-task-mark", "task_mark_enabled", None,
         "write the task key into the export (default: on)",
         action="bool_pair"),
    Flag("--continue-members/--no-continue-members",
         "continue_members_across_targets", None,
         "retry a contact on the next target after it failed on one",
         action="bool_pair"),
    Flag("--invite-interval-min", "invite_interval_min", float,
         "minimum invite interval"),
    Flag("--invite-interval-max", "invite_interval_max", float,
         "maximum invite interval"),
    Flag("--flood-cap", "flood_cap", int, "PEER_FLOOD retries per handler"),
    Flag("--invitee-flood-cap", "invitee_flood_cap", int,
         "PEER_FLOOD retries per invitee"),
    Flag("--export-flood-timeout", "export_flood_timeout", str,
         "where the PEER_FLOOD and TIMEOUT record goes"),
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
    #: ``maxInviteIntervalMsec``: 邀请间隔的上界，毫秒。
    max_invite_interval_ms: int = 0
    #: ``peerFloodTargetMaxRetries``: 某个群 PEER_FLOOD 时的重试封顶。
    peer_flood_target_max_retries: int = 3
    task_name: str = "batch-invite-v2"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> InviteV2Config:
        del settings
        data = section(raw, SECTION)
        targets = [t.ref for t in targets_of(data, kind="group")]
        raw_contacts = data.get("contacts")
        if isinstance(raw_contacts, str):
            contacts = [part.strip()
                        for part in raw_contacts.replace("\n", ",").split(",")
                        if part.strip()]
        elif isinstance(raw_contacts, (list, tuple)):
            contacts = [str(item) for item in raw_contacts]
        else:
            contacts = []
        contacts.extend(_file_lines(data, "contact_files"))
        if not targets:
            raise ConfigError(f"{SECTION}: no target group given")
        mode = str(data.get("member_input_mode") or "manual").lower()
        if mode not in ("manual", "text", "file"):
            raise ConfigError(
                f"{SECTION}: member_input_mode must be manual, text or file, "
                f"is {mode}")
        if mode == "file":
            file_name = text(data, "member_file")
            if not file_name:
                raise ConfigError(
                    f"{SECTION}: member_input_mode=file needs member_file")
            contacts.extend(_file_lines(data, "member_file"))
        limit_type = str(data.get("limit_count_type") or "success").lower()
        if limit_type not in ("success", "exec"):
            raise ConfigError(
                f"{SECTION}: limit_count_type must be success or exec, "
                f"is {limit_type}")
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
            member_input_mode=mode,
            member_file=text(data, "member_file"),
            continue_members_across_targets=bool(
                data.get("continue_members_across_targets", True)),
            wait_minutes=num(data, "wait_minutes", 0.0, minimum=0.0),
            limit_count_type=limit_type,
            custom_task_key_enabled=bool(data.get("custom_task_key_enabled")),
            custom_task_key=text(data, "custom_task_key"),
            task_mark_enabled=bool(data.get("task_mark_enabled", True)),
            invites_per_request=num(data, "invites_per_request", 1, kind=int,
                                     minimum=1),
            max_invite_interval_ms=num(data, "max_invite_interval_ms", 0,
                                       kind=int, minimum=0),
            peer_flood_target_max_retries=num(
                data, "peer_flood_target_max_retries", 3, kind=int, minimum=0),
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


def build_jobs(targets: list[str], contacts: list[str], handlers: list[str],
               *, target_success_cap: int = 0,
               exec_cap: int = 0,
               cap_by: str = "success") -> list[tuple[str, str, str]]:
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
    return max(config.request_interval,
               generator.uniform(config.invite_interval_min,
                                 config.invite_interval_max))


def _invite_lane(sender: object, ctx: RunContext, task: object,
                 lane: list[tuple[str, str, str]], total: int,
                 config: InviteV2Config, backoff: PeerFloodBackoff,
                 waited: list[float]
                 ) -> tuple[int, int, list[str], list[str]]:
    """One lane: its own handler accounts, its own pace, its own tallies."""
    ok = failed = 0
    flood: list[str] = []
    timeouts: list[str] = []
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
    previous_target = ""
    for index, (handler, group, contact) in enumerate(lane, start=1):
        if config.success_cap and ok >= config.success_cap:
            print(f"stopped: success_cap ({config.success_cap})")
            break
        if handler in rested:
            record(ctx, task, handler, f"{group}<-{contact}", False,
                   "HANDLER_REST")
            print(f"[{index:03d}/{total:03d}] skipped {contact} in {group} "
                  f"(handler rested after {handler_floods.get(handler, 0)} "
                  f"PEER_FLOOD)")
            continue
        if group in exhausted:
            record(ctx, task, handler, f"{group}<-{contact}", False, "TARGET_REST")
            print(f"[{index:03d}/{total:03d}] skipped {contact} in {group} "
                  f"(target rested after "
                  f"{target_floods.get(group, 0)} PEER_FLOOD)")
            continue
        if not config.continue_members_across_targets and contact in abandoned:
            print(f"[{index:03d}/{total:03d}] skipped {contact} "
                  f"(already failed on another target)")
            continue
        if index > 1:
            if group != previous_target and config.wait_minutes:
                # waitMinutes is a pause between two targets, not between two
                # invites: it is the page's "let the last group settle" knob.
                rest = config.wait_minutes * 60.0
                waited.append(rest)
                pause(ctx, rest)
            elif index % max(1, config.invites_per_request) == 1 \
                    or config.invites_per_request == 1:
                delay = gap(config, rng)
                waited.append(delay)
                pause(ctx, delay)
        previous_target = group
        target = parse_many([group], "group")[0]
        member = parse_many([contact], "custom")[0]
        floods = 0
        while True:
            result = sender.invite(handler, target, member)  # type: ignore[attr-defined]
            if result.code != "PEER_FLOOD" or floods >= config.invitee_flood_cap:
                break
            floods += 1
            wait = backoff.wait_for(floods - 1)
            print(f"  PEER_FLOOD backoff {wait:.0f}s "
                  f"(attempt {floods}/{config.invitee_flood_cap}) {contact}")
            if ctx.live:
                time.sleep(wait)
        if result.ok:
            ok += 1
            record(ctx, task, handler, f"{group}<-{contact}", True)
            print(f"[{index:03d}/{total:03d}] handler={handler} "
                  f"target={group} contact={contact} -> ok")
            continue
        failed += 1
        record(ctx, task, handler, f"{group}<-{contact}", False, result.code,
               result.detail)
        print(f"[{index:03d}/{total:03d}] handler={handler} "
              f"target={group} contact={contact} -> {result.code}")
        if not config.continue_members_across_targets:
            abandoned.add(contact)
        if result.code == "PEER_FLOOD":
            handler_floods[handler] = handler_floods.get(handler, 0) + 1
            if config.flood_cap and handler_floods[handler] >= config.flood_cap:
                rested.add(handler)
                print(f"handler rested: {handler} answered PEER_FLOOD "
                      f"{handler_floods[handler]} time(s) "
                      f"(flood_cap={config.flood_cap})")
            target_floods[group] = target_floods.get(group, 0) + 1
            cap = config.peer_flood_target_max_retries
            if cap and target_floods[group] >= cap:
                exhausted.add(group)
                print(f"target rested: {group} answered PEER_FLOOD "
                      f"{target_floods[group]} time(s) "
                      f"(peer_flood_target_max_retries={cap})")
            if contact not in flood:
                flood.append(contact)
        if result.code == "TIMEOUT" and contact not in timeouts:
            timeouts.append(contact)
    return ok, failed, flood, timeouts


def run(config: InviteV2Config, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    handlers = [account.label for account in accounts] or ["-"]
    jobs = build_jobs(config.targets, config.contacts, handlers,
                      target_success_cap=config.target_success_cap,
                      exec_cap=config.exec_cap,
                      cap_by=config.limit_count_type)

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
    if config.max_invite_interval_ms:
        print(f"max invite interval: {config.max_invite_interval_ms} ms")

    task = open_task(ctx, config.task_name, FEATURE,
                     {"targets": config.targets, "contacts": config.contacts},
                     len(jobs))
    sender = ctx.sender()

    ok = failed = 0
    backoff = PeerFloodBackoff()
    flood: list[str] = []
    timeouts: list[str] = []
    waited: list[float] = []
    if not jobs:
        # An empty plan is a legitimate answer (nothing matched the filters), not
        # a crash: there is simply no lane to build.
        print("no jobs: nothing to invite (check the target and member lists)")
        print("PLAN: 0 invites, failed 0")
        close_task(ctx, task, ok=0, failed=0)
        return 0
    lanes = lanes_by(lambda job: job[0], jobs, config.threads)
    if len(lanes) > 1:
        print(f"threads: {len(lanes)} lanes, one handler account each")
        with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
            work = [pool.submit(_invite_lane, sender, ctx, task, lane,
                                len(jobs), config, backoff, waited)
                    for lane in lanes]
            for job in work:
                done_ok, done_failed, done_flood, done_timeout = job.result()
                ok += done_ok
                failed += done_failed
                flood.extend(done_flood)
                timeouts.extend(done_timeout)
    else:
        ok, failed, flood, timeouts = _invite_lane(
            sender, ctx, task, lanes[0], len(jobs), config, backoff, waited)

    print(f"ok: {ok}  failed: {failed}  PEER_FLOOD: {len(flood)}  "
          f"TIMEOUT: {len(timeouts)}")
    if ctx.live and waited:
        print(f"waited: {format_duration(sum(waited))}")
    print(f"PLAN: {len(jobs)} invites, {len(handlers)} handlers, "
          f"exec {ok + failed}")

    base = out_dir(ctx, {"output_dir": ""}, ctx.settings)
    path = config.export_flood_timeout or f"{base}/batch_invite_v2_flood.txt"
    # A key that was typed is meant to be used; the page's separate "enabled"
    # switch only matters when a key comes from a stored config.
    mark = (config.custom_task_key
            if config.task_mark_enabled
            and (config.custom_task_key or config.custom_task_key_enabled)
            else "")
    rows = [f"PEER_FLOOD\t{item}\t{mark}" for item in flood]
    rows += [f"TIMEOUT\t{item}\t{mark}" for item in timeouts]
    export(path, rows, "PEER_FLOOD and TIMEOUT")
    close_task(ctx, task, ok=ok, failed=failed)
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(InviteV2Config.from_dict(raw, ctx.settings), ctx)


__all__ = ["InviteV2Config", "TITLE", "build_jobs", "gap", "main", "run"]
