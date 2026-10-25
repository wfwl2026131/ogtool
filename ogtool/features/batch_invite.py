"""#11 batch invite - invite members into target groups with a pool of handlers.

Fields from the page: groups, members, invite strategy, minimum request
interval, concurrent accounts, per target success cap, handler caps (success and
executions), minimum invite interval, the admin account, the two PEER_FLOOD
caps (invitee and handler), the add-contact wait, and the three exports
(MISSING, FLOOD, report).

The strategy is the text the page prints under the toggle: with ``continue`` a
new target group resumes from the members the previous one did not reach; with
``loop`` every group starts again at the first member.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..config import ConfigError, Settings
from ..flags import Flag
from ..limiter import PeerFloodBackoff, format_duration
from ..report import export, write_report
from ..runtime import RunContext
from ..targets import parse_many
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    lanes_by,
    lines,
    name_of,
    num,
    open_task,
    out_dir,
    pause,
    read_list_file,
    record,
    section,
    text,
)

FEATURE = "batch_invite"
SECTION = "batch_invite"
TITLE = "#11 批量拉人"

STRATEGY_CONTINUE = "continue"
STRATEGY_LOOP = "loop"

FLAGS = (
    Flag("--groups", "groups", str, "target groups, comma separated"),
    Flag("--groups-file", "group_files", str, "file with one group per line",
         action="append"),
    Flag("--members", "members", str, "members, comma separated"),
    Flag("--members-file", "member_files", str, "file with one member per line",
         action="append"),
    Flag("--strategy", "strategy", str, "continue|loop"),
    Flag("--request-interval", "request_interval", float,
         "minimum seconds between two requests"),
    Flag("--threads", "threads", int,
         "planning only; use batch_invite_v2 for one lane per account"),
    Flag("--target-success-cap", "target_success_cap", int,
         "invites one target group accepts"),
    Flag("--handler-success-cap", "handler_success_cap", int,
         "successes one handler may reach"),
    Flag("--handler-exec-cap", "handler_exec_cap", int,
         "attempts one handler may spend"),
    Flag("--invite-interval", "invite_interval", float,
         "minimum seconds between two invites"),
    Flag("--admin-account", "admin_account", str, "the admin account"),
    Flag("--member-flood-cap", "member_flood_cap", int,
         "PEER_FLOOD retries per invitee"),
    Flag("--handler-flood-cap", "handler_flood_cap", int,
         "PEER_FLOOD retries per handler"),
    Flag("--add-contact-wait", "add_contact_wait", float,
         "seconds to wait after adding a contact"),
    Flag("--export-missing", "export_missing", str,
         "where the MISSING list goes"),
    Flag("--export-flood", "export_flood", str, "where the FLOOD list goes"),
    Flag("--report", "report", None, "write the run report", action="store_true"),
)


@dataclass(slots=True)
class InviteConfig:
    account_labels: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    members: list[str] = field(default_factory=list)
    strategy: str = STRATEGY_CONTINUE
    request_interval: float = 10.0
    threads: int = 1
    target_success_cap: int = 0
    handler_success_cap: int = 0
    handler_exec_cap: int = 0
    invite_interval: float = 10.0
    admin_account: str = ""
    member_flood_cap: int = 2
    handler_flood_cap: int = 3
    add_contact_wait: float = 5.0
    export_missing: str = ""
    export_flood: str = ""
    report: bool = False
    task_name: str = "batch-invite"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> InviteConfig:
        del settings
        data = section(raw, SECTION)
        groups = lines(data, "groups")
        groups.extend(_file_lines(data, "group_files"))
        members = lines(data, "members")
        members.extend(_file_lines(data, "member_files"))
        if not groups:
            raise ConfigError(f"{SECTION}: no groups given")
        if not members:
            raise ConfigError(f"{SECTION}: no members given")
        return cls(
            account_labels=accounts_of(raw, data),
            groups=_unique(groups),
            members=_unique(members),
            strategy=str(data.get("strategy") or STRATEGY_CONTINUE),
            request_interval=num(data, "request_interval", 10.0),
            threads=num(data, "threads", 1, kind=int, minimum=1),
            target_success_cap=num(data, "target_success_cap", 0, kind=int),
            handler_success_cap=num(data, "handler_success_cap", 0, kind=int),
            handler_exec_cap=num(data, "handler_exec_cap", 0, kind=int),
            invite_interval=num(data, "invite_interval", 10.0),
            admin_account=text(data, "admin_account"),
            member_flood_cap=num(data, "member_flood_cap", 2, kind=int),
            handler_flood_cap=num(data, "handler_flood_cap", 3, kind=int),
            add_contact_wait=num(data, "add_contact_wait", 5.0),
            export_missing=text(data, "export_missing"),
            export_flood=text(data, "export_flood"),
            report=bool(data.get("report", False)),
            task_name=name_of(raw, data, "batch-invite"),
        )


def _file_lines(data: dict, key: str) -> list[str]:
    files = data.get(key) or []
    if isinstance(files, str):
        files = [files]
    out: list[str] = []
    for path in files:
        out.extend(read_list_file(str(path)))
    return out


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def build_jobs(groups: list[str], members: list[str], handlers: list[str],
               strategy: str, *,
               target_success_cap: int = 0,
               handler_exec_cap: int = 0,
               handler_success_cap: int = 0) -> list[tuple[str, str, str]]:
    """``(handler, group, member)`` triples, honouring the caps and strategy."""
    jobs: list[tuple[str, str, str]] = []
    if not handlers:
        return jobs
    used: dict[str, int] = {name: 0 for name in handlers}
    success: dict[str, int] = {name: 0 for name in handlers}
    cursor = 0
    for group in groups:
        start = cursor if strategy == STRATEGY_CONTINUE else 0
        pool = members[start:]
        for taken, member in enumerate(pool, start=1):
            if target_success_cap and taken > target_success_cap:
                break
            candidates = [
                name for name in handlers
                if not handler_exec_cap or used[name] < handler_exec_cap
                if not handler_success_cap or success[name] < handler_success_cap
            ]
            if not candidates:
                return jobs
            handler = candidates[len(jobs) % len(candidates)]
            if strategy == STRATEGY_CONTINUE:
                cursor = start + taken
            jobs.append((handler, group, member))
            used[handler] += 1
    return jobs


def gap(config: InviteConfig) -> float:
    """Seconds between two invites.

    ``request_interval`` paces the pool and ``invite_interval`` paces the
    invite call itself; the serial run has to clear both, so the longer wins.
    """
    return max(config.request_interval, config.invite_interval)


def _invite_lane(sender: object, ctx: RunContext, task: int,
                 lane: list[tuple[str, str, str]], total: int,
                 config: InviteConfig,
                 backoff: PeerFloodBackoff) -> tuple[int, int, list[str],
                                                   list[str], float]:
    """One handler's share of the queue: the page's ``threads`` lanes.

    Every job of one handler lives in exactly one lane, so ``handlerFloodCap``
    and the pacing stay promises the server can check.
    """
    ok = failed = 0
    missing: list[str] = []
    flood: list[str] = []
    waited = 0.0
    handler_floods: dict[str, int] = {}
    rested: set[str] = set()
    previous: str | None = None
    for index, (handler, group, member) in enumerate(lane, start=1):
        if handler in rested:
            record(ctx, task, handler, f"{group}<-{member}", False,
                   "HANDLER_REST")
            print(f"skipped {member} in {group} for {handler} "
                  f"(handler rested after {handler_floods.get(handler, 0)} "
                  f"PEER_FLOOD)")
            continue
        if previous is not None:
            # ``addContactWait`` is the pause a handler takes after the page
            # added a missing member to its contacts.
            delay = gap(config)
            if previous != group and config.add_contact_wait:
                delay = max(delay, config.add_contact_wait)
            waited += delay
            pause(ctx, delay)
        previous = group
        target = parse_many([group], "group")[0]
        invitee = parse_many([member], "custom")[0]
        floods = 0
        while True:
            result = sender.invite(handler, target, invitee)  # type: ignore[attr-defined]
            if result.code != "PEER_FLOOD" or floods >= config.member_flood_cap:
                break
            floods += 1
            wait = backoff.wait_for(floods - 1)
            print(f"  PEER_FLOOD backoff {wait:.0f}s "
                  f"(attempt {floods}/{config.member_flood_cap}) {member}")
            if ctx.live:
                time.sleep(wait)
        if result.ok:
            ok += 1
            record(ctx, task, handler, f"{group}<-{member}", True)
            print(f"[{index:03d}/{total:03d}] handler={handler} "
                  f"group={group} member={member} -> ok")
            continue
        failed += 1
        record(ctx, task, handler, f"{group}<-{member}", False, result.code,
               result.detail)
        print(f"[{index:03d}/{total:03d}] handler={handler} "
              f"group={group} member={member} -> {result.code}")
        if (result.code in ("USER_PRIVACY_RESTRICTED",
                            "USER_NOT_MUTUAL_CONTACT", "USER_ID_INVALID")
                and member not in missing):
            missing.append(member)
        if result.code == "PEER_FLOOD":
            if member not in flood:
                flood.append(member)
            handler_floods[handler] = handler_floods.get(handler, 0) + 1
            if config.handler_flood_cap \
                    and handler_floods[handler] >= config.handler_flood_cap:
                rested.add(handler)
                print(f"handler rested: {handler} answered PEER_FLOOD "
                      f"{handler_floods[handler]} time(s) "
                      f"(handler_flood_cap={config.handler_flood_cap})")
    return ok, failed, missing, flood, waited


def run(config: InviteConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    handlers = [account.label for account in accounts] or ["-"]
    jobs = build_jobs(
        config.groups, config.members, handlers, config.strategy,
        target_success_cap=config.target_success_cap,
        handler_exec_cap=config.handler_exec_cap,
        handler_success_cap=config.handler_success_cap,
    )

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        groups=len(config.groups),
        members=len(config.members),
        strategy=config.strategy,
        request_interval=f"{config.request_interval:.0f}s",
        invite_interval=f"{config.invite_interval:.0f}s",
        concurrency=config.threads,
        target_success_cap=config.target_success_cap or "-",
        handler_flood_cap=config.handler_flood_cap,
        member_flood_cap=config.member_flood_cap,
        admin=config.admin_account or "-",
        add_contact_wait=f"{config.add_contact_wait:.0f}s",
        queue=len(jobs),
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"groups": config.groups, "members": config.members,
                      "strategy": config.strategy}, len(jobs))
    sender = ctx.sender()

    backoff = PeerFloodBackoff()
    ok = failed = 0
    missing: list[str] = []
    flood: list[str] = []
    waited = 0.0
    lanes = lanes_by(lambda job: job[0], jobs, config.threads)
    if len(lanes) > 1:
        # ``threads``: one lane per handler account, never one handler in two
        # lanes - that is what makes the pacing a promise and not a suggestion.
        print(f"threads: {len(lanes)} lanes, one handler account each")
        with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
            work = [pool.submit(_invite_lane, sender, ctx, task, lane, len(jobs),
                                config, backoff)
                    for lane in lanes]
            for job in work:
                done_ok, done_failed, done_missing, done_flood, done_waited = \
                    job.result()
                ok += done_ok
                failed += done_failed
                waited += done_waited
                for item in done_missing:
                    if item not in missing:
                        missing.append(item)
                for item in done_flood:
                    if item not in flood:
                        flood.append(item)
    else:
        ok, failed, missing, flood, waited = _invite_lane(
            sender, ctx, task, lanes[0] if lanes else [], len(jobs), config,
            backoff)

    print(f"成功: {ok}  执行数: {failed + ok}  失败: {failed}")
    if ctx.live and waited:
        print(f"waited: {format_duration(waited)}")
    print(f"MISSING: {len(missing)}")
    print(f"FLOOD: {len(flood)}")
    print(f"PLAN: {len(jobs)} invites, {len(handlers)} handlers, "
          f"MISSING {len(missing)}, FLOOD {len(flood)}")

    base = out_dir(ctx, {"output_dir": ""}, ctx.settings)
    export(config.export_missing or f"{base}/batch_invite_missing.txt",
           missing, "MISSING")
    export(config.export_flood or f"{base}/batch_invite_flood.txt",
           flood, "FLOOD")
    if config.report:
        write_report(f"{base}/{config.task_name}-report.md", TITLE, {
            "成功": [f"{g} <- {m}" for _, g, m in jobs[:ok]],
            "MISSING": missing,
            "FLOOD": flood,
        })
    close_task(ctx, task, ok=ok, failed=failed)
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(InviteConfig.from_dict(raw, ctx.settings), ctx)


__all__ = [
    "InviteConfig",
    "STRATEGY_CONTINUE",
    "STRATEGY_LOOP",
    "TITLE",
    "build_jobs",
    "gap",
    "main",
    "run",
]
