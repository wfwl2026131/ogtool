"""#T2 group manage - batch transfer group ownership, then leave.

The reference's Group Manager page opens from the console ("Open Group
Manager") and says itself what it is for: ``Group management: create groups,
transfer, and view stats.`` Its transfer block is the atomic
``tg.answer_group.transfer_owner_and_leave`` relay endpoint (:1743915), and
the run log prints one line per step:

* ``transfer_owner_and_leave: task_id=%1 op_id=%2 account=%3 phone=%4 group=%5``
  (:1743952) - the dispatch line;
* the new owner joins first - ``Target account failed to join group %1 before
  transfer.`` stops that group;
* the ownership edit needs the source account's 2FA password - without one
  the account is skipped whole (``Skip account %1: missing second
  password.``);
* after a successful edit the old owner leaves - ``Original owner left group
  %1.`` / ``Original owner failed to leave group %1.`` (the transfer still
  counts, ``left=%4`` is just 0);
* the per-group verdicts ``Transferred group %1 from %2 to %3 (%4)`` and
  ``Transfer owner failed for group %1: %2``, the ``auto_transfer_ok`` /
  ``auto_transfer_failed`` lines (:1743960-:1743961), and the tail
  ``Transferred %1 groups`` + ``Transfer task finished.``

Ownership itself is the Telegram creator-edit call (``messages.editChatCreator``
in the current schema generation; older layers knew it as
``channels.editCreator``), which only
exists for broadcast channels and megagroups - a basic group cannot hand its
creator over, and the reference's own dialog refuses those the same way
(``Unexpected: Chat type in EditAdminBox::canTransferOwnership.``,
:1724715). The reference's ``group_mgr`` table (:1723421) keeps one row per
group with ``owner_phone`` and ``old_owner`` columns; this page updates
exactly those columns on every transfer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..config import ConfigError, Settings
from ..flags import Flag
from ..limiter import format_duration
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
    pause,
    read_list_file,
    record,
    section,
    text,
)

FEATURE = "group_manage"
SECTION = "group_manage"
TITLE = "#T2 群管家"

FLAGS = (
    Flag("--groups", "groups", str, "comma separated groups to transfer"),
    Flag("--groups-file", "groups_file", str, "file with one group per line"),
    Flag(
        "--from-store",
        "from_store",
        None,
        "take the groups the source owns from the group_mgr table",
        action="bool_pair",
    ),
    Flag("--target", "target", str, "the new owner: an account label or a user ref"),
    Flag("--password", "password", str, "the source account's 2FA password"),
    Flag(
        "--password-env",
        "password_env",
        str,
        "environment variable holding the 2FA password",
    ),
    Flag("--interval-ms", "interval_ms", int, "pause between groups in milliseconds"),
)


@dataclass(slots=True)
class GroupConfig:
    account_labels: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    from_store: bool = False
    target: str = ""
    password: str = ""
    interval_ms: int = 1000
    task_name: str = "group-manage"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> GroupConfig:
        del settings
        data = section(raw, SECTION)
        # A dry run with an empty pool still prints a plan (that is what
        # makes the sweep deterministic on a bare checkout); --live turns
        # the empty bind into the pool's own SESSION_NOT_FOUND error.
        account_labels = accounts_of(raw, data)
        target = text(data, "target").strip()
        if not target:
            raise ConfigError(f"{SECTION}: Please enter target username.")
        groups = text(data, "groups", "")
        group_items = [
            item.strip()
            for item in groups.replace(",", "\n").splitlines()
            if item.strip()
        ]
        group_file = text(data, "groups_file")
        if group_file:
            group_items.extend(
                item for item in read_list_file(group_file) if item.strip()
            )
        from_store = bool(data.get("from_store"))
        if not group_items and not from_store:
            raise ConfigError(
                f"{SECTION}: give the groups to transfer "
                "(--groups, --groups-file or --from-store)"
            )
        password = str(data.get("password") or "")
        password_env = str(data.get("password_env") or "")
        if password_env:
            password = os.environ.get(password_env, "")
        return cls(
            account_labels=account_labels,
            groups=group_items,
            from_store=from_store,
            target=target,
            password=password,
            interval_ms=num(data, "interval_ms", 1000, kind=int, minimum=0),
            task_name=name_of(raw, data, "group-manage"),
        )


def run(config: GroupConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts] or ["-"]
    owner_phone = {account.label: account.phone for account in accounts}

    new_owner_ref = config.target
    new_owner_phone = ""
    if config.target:
        try:
            bound = ctx.bind([config.target])
        except Exception:  # noqa: BLE001 - an outside user ref is fine too
            bound = []
        if bound:
            # --target is one of our own accounts: it joins the group itself
            # and the source session resolves it by phone.
            new_owner_phone = bound[0].phone or config.target
            new_owner_ref = new_owner_phone

    header(
        config.task_name,
        target=config.target or "-",
        new_owner=new_owner_ref or "-",
        groups=str(len(config.groups)) + (" + store" if config.from_store else ""),
        interval=f"{config.interval_ms} ms",
        accounts=f"{len(labels)}"
        + (" (no account bound, plan only)" if not accounts else ""),
    )

    task = open_task(
        ctx,
        config.task_name,
        FEATURE,
        {"target": config.target, "groups": len(config.groups)},
        len(labels),
    )
    sender = ctx.sender()
    store = ctx.store

    planned: list[str] = list(config.groups)
    if config.from_store:
        for label in labels:
            owner = owner_phone.get(label, label)
            planned.extend(
                str(row["group_id"]) for row in store.groups_for_owner(owner)
            )
    seen: set[str] = set()
    deduped: list[str] = []
    for ref in planned:
        if ref not in seen:
            seen.add(ref)
            deduped.append(ref)
    planned = deduped

    transferred = 0
    failed = 0
    waited = 0.0
    for index, ref in enumerate(planned, start=1):
        if index > 1 and config.interval_ms:
            delay = config.interval_ms / 1000.0
            waited += delay
            pause(ctx, delay)
        try:
            group_target = parse_many([ref], "group")[0]
        except Exception:  # noqa: BLE001 - an unparseable ref is skipped
            print(f"Invalid group id {ref}, skip transfer.")
            failed += 1
            record(ctx, task, "-", ref, False, "INVALID_GROUP", "Invalid group id")
            continue
        for label in labels:
            print(
                f"transfer_owner_and_leave: task={task.id} account={label}"
                f" phone={owner_phone.get(label, '-')} group={ref}"
            )
        if not config.password:
            for label in labels:
                print(f"Skip account {label}: missing second password.")
            failed += 1
            record(
                ctx,
                task,
                ",".join(labels),
                ref,
                False,
                "SECOND_PASSWORD",
                "missing second password",
            )
            continue

        # The new owner must sit in the group before the edit: a bound
        # account joins by itself, an outside user is invited by the source.
        # Both paths are idempotent on already-participant.
        if new_owner_phone:
            join = sender.join(config.target, group_target)
        else:
            join = sender.invite(
                labels[0], group_target, parse_many([new_owner_ref], "user")[0]
            )
        if not join.ok:
            print(f"Target account failed to join group {ref} before transfer.")
            failed += 1
            record(ctx, task, config.target, ref, False, join.code, join.detail)
            continue

        transfer = sender.edit_creator(
            labels[0], group_target, new_owner_ref, config.password
        )
        if not transfer.ok:
            print(f"Transfer owner failed for group {ref}: {transfer.detail}")
            print(
                f"auto_transfer_failed: "
                f"phone={owner_phone.get(labels[0], '-')} peer={ref}"
                f" new_owner={new_owner_ref} err={transfer.code}"
                f" msg={transfer.detail}"
            )
            failed += 1
            record(ctx, task, labels[0], ref, False, transfer.code, transfer.detail)
            continue

        chat_kind = transfer.detail or "channel"
        left = 0
        leave = sender.leave(labels[0], group_target)
        if leave.ok:
            print(f"Original owner left group {ref}.")
            left = 1
        else:
            print(f"Original owner failed to leave group {ref}.")
        print(
            f"Transferred group {ref} from {labels[0]} to {new_owner_ref} ({chat_kind})"
        )
        print(
            f"auto_transfer_ok: phone={owner_phone.get(labels[0], '-')}"
            f" peer={ref} new_owner={new_owner_ref} left={left}"
        )
        transferred += 1
        record(ctx, task, labels[0], ref, True)
        store.upsert_group_owner(
            group_id=ref,
            owner_phone=new_owner_phone or new_owner_ref,
            old_owner=owner_phone.get(labels[0], labels[0]),
            chat_type=2 if chat_kind in ("channel", "megagroup") else 1,
            status="transferred",
        )

    print(f"Transferred {transferred} groups")
    print("Transfer task finished.")
    if ctx.live and waited:
        print(f"waited: {format_duration(waited)}")
    print(f"PLAN: {len(planned)} groups, {transferred} transferred, {failed} failed")
    close_task(ctx, task, ok=transferred, failed=failed)
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(GroupConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["FEATURE", "GroupConfig", "SECTION", "TITLE", "main", "run"]
