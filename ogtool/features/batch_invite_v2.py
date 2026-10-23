"""#17 batch invite v2 - invite your own contacts into groups you own.

The page is the second generation of #11: the source is the account's own
contact list instead of a member file, and the exports are the PEER_FLOOD and
TIMEOUT records.  Fields: target, minimum request interval, threads, per target
success cap, success cap, execution cap, invite interval range, the two
PEER_FLOOD caps and the export path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..config import ConfigError, Settings
from ..flags import Flag
from ..limiter import PeerFloodBackoff
from ..report import export
from ..runtime import RunContext
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
    Flag("--threads", "threads", int, "threads"),
    Flag("--target-success-cap", "target_success_cap", int,
         "invites one target accepts"),
    Flag("--success-cap", "success_cap", int, "total successes"),
    Flag("--exec-cap", "exec_cap", int, "total attempts"),
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
               exec_cap: int = 0) -> list[tuple[str, str, str]]:
    """Round robin handlers over contacts, one target at a time."""
    jobs: list[tuple[str, str, str]] = []
    if not handlers or not contacts:
        return jobs
    for target in targets:
        for taken, member in enumerate(contacts, start=1):
            if target_success_cap and taken > target_success_cap:
                break
            if exec_cap and len(jobs) >= exec_cap:
                return jobs
            handler = handlers[len(jobs) % len(handlers)]
            jobs.append((handler, target, member))
    return jobs


def run(config: InviteV2Config, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    handlers = [account.label for account in accounts] or ["-"]
    jobs = build_jobs(config.targets, config.contacts, handlers,
                      target_success_cap=config.target_success_cap,
                      exec_cap=config.exec_cap)

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
        queue=len(jobs),
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"targets": config.targets, "contacts": config.contacts},
                     len(jobs))
    sender = ctx.sender()

    ok = failed = 0
    backoff = PeerFloodBackoff()
    flood: list[str] = []
    timeouts: list[str] = []
    for index, (handler, group, contact) in enumerate(jobs, start=1):
        if config.success_cap and ok >= config.success_cap:
            print(f"stopped: success_cap ({config.success_cap})")
            break
        target = parse_many([group], "group")[0]
        member = parse_many([contact], "custom")[0]
        floods = 0
        while True:
            result = sender.invite(handler, target, member)
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
            print(f"[{index:03d}/{len(jobs):03d}] handler={handler} "
                  f"target={group} contact={contact} -> ok")
        else:
            failed += 1
            record(ctx, task, handler, f"{group}<-{contact}", False, result.code)
            print(f"[{index:03d}/{len(jobs):03d}] handler={handler} "
                  f"target={group} contact={contact} -> {result.code}")
            if result.code == "PEER_FLOOD" and contact not in flood:
                flood.append(contact)
            if result.code == "TIMEOUT" and contact not in timeouts:
                timeouts.append(contact)

    print(f"ok: {ok}  failed: {failed}  PEER_FLOOD: {len(flood)}  "
          f"TIMEOUT: {len(timeouts)}")
    print(f"PLAN: {len(jobs)} invites, {len(handlers)} handlers, "
          f"exec {ok + failed}")

    base = out_dir(ctx, {"output_dir": ""}, ctx.settings)
    path = config.export_flood_timeout or f"{base}/batch_invite_v2_flood.txt"
    export(path, [f"PEER_FLOOD\t{item}" for item in flood]
           + [f"TIMEOUT\t{item}" for item in timeouts],
           "PEER_FLOOD and TIMEOUT")
    close_task(ctx, task, ok=ok, failed=failed)
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(InviteV2Config.from_dict(raw, ctx.settings), ctx)


__all__ = ["InviteV2Config", "TITLE", "build_jobs", "main", "run"]
