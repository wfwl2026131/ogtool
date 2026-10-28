"""#19 puppet chat v2 - the owner accounts play the script into their own chats.

Same player as #0 with three differences the page makes visible: the targets
must be groups or channels the accounts own, the accounts are started one after
another (``startup interval``) instead of together, and there is no polish switch
because nothing is rewritten on this page.

On top of the player this page carries the executor the reference runs it with:
``PuppetChatExecutor started - Task ID: %1, %2 groups, %3 cycles, %4 patterns``
- several pattern files in one task (``--patterns``), whole-set repeats
(``--cycles``), a stats file per cycle (``puppet_chat_%1_%2.json``) that makes a
stopped run resumable (``--resume``), a message wait floor (``Waiting %1
seconds before next message``), the puppet-account mapping table with its
remap rules (``SlotMap``) and a closing speech window that logs what arrives in
the own groups after the lines are done (``handleReceivedMessage`` /
``speechLogged``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .. import chat_pattern
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
    section,
    targets_of,
    text,
)
from .puppet_chat import (
    PuppetConfig,
    SlotMap,
    StatsFile,
    play,
    resolve_pattern,
    schedule_wait,
)

FEATURE = "puppet_chat_v2"
SECTION = "puppet_chat_v2"
TITLE = "#19 话术炒群v2"

STATS_DIR = "puppet_chat"

FLAGS = (
    Flag("--targets", "targets", str, "own groups/channels, comma separated"),
    Flag("--targets-file", "files", str, "file with one target per line",
         action="append"),
    Flag("--pattern", "pattern", str, "script file (话术文件)"),
    Flag("--patterns", "patterns", str, "extra script files (repeatable)",
         action="append"),
    Flag("--mode-file", "mode_file", str, "mode file, optional"),
    Flag("--mode", "mode", str, "mode name inside the mode file"),
    Flag("--startup-interval", "startup_interval", float,
         "seconds between two accounts starting"),
    Flag("--loop-interval", "loop_interval", float,
         "minutes between two loops"),
    Flag("--loops", "loops", int, "how many times the script plays"),
    Flag("--cycles", "cycles", int,
         "executor cycles over every group and pattern"),
    Flag("--cycle-interval", "cycle_interval", float,
         "seconds between two cycles"),
    Flag("--message-wait", "message_wait", float,
         "seconds before next message (floor between sends)"),
    Flag("--multiplier", "multiplier", int, "message multiplier"),
    Flag("--daily-limit", "daily_limit", int,
         "text messages one account may send per day"),
    Flag("--stats-dir", "stats_dir", str,
         "where cycle stats are written (default data/puppet_chat)"),
    Flag("--resume/--no-resume", "resume", None,
         "skip lines the receipts already recorded as sent",
         action="bool_pair"),
    Flag("--listen", "listen", int,
         "log incoming speech for N seconds after the run (0 = off)"),
)


@dataclass(slots=True)
class PuppetV2Config:
    account_labels: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    pattern: str = ""
    patterns: list[str] = field(default_factory=list)
    mode_file: str = ""
    mode: str = ""
    startup_interval: float = 10.0
    loop_interval: float = 5.0
    loops: int = 1
    cycles: int = 1
    cycle_interval: float = 0.0
    message_wait: float = 0.0
    multiplier: int = 1
    daily_limit: int = 0
    stats_dir: str = ""
    resume: bool = False
    listen: int = 0
    pictures: str = ""
    #: ``scheduleDateTime``: hold the run until this wall clock time.
    schedule_at: str = ""
    #: ``disableLinkPreview``: no link card on the lines that carry a URL.
    disable_link_preview: bool = False
    task_name: str = "puppet-chat-v2"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> PuppetV2Config:
        data = section(raw, SECTION)
        targets = [target.ref for target in targets_of(data, kind="group")]
        if not targets:
            raise ConfigError(f"{SECTION}: no target given (--targets)")
        if not text(data, "pattern") and not text(data, "mode_file"):
            raise ConfigError(f"{SECTION}: no script or mode file given")
        pictures = text(data, "pictures") or str(
            chat_pattern.default_picture_dir(settings.data_dir))
        return cls(
            account_labels=accounts_of(raw, data),
            targets=targets,
            pattern=text(data, "pattern"),
            patterns=_as_list(data.get("patterns")),
            mode_file=text(data, "mode_file"),
            mode=text(data, "mode"),
            startup_interval=num(data, "startup_interval", 10.0),
            loop_interval=num(data, "loop_interval", 5.0),
            loops=num(data, "loops", 1, kind=int, minimum=1),
            cycles=num(data, "cycles", 1, kind=int, minimum=1),
            cycle_interval=num(data, "cycle_interval", 0.0),
            message_wait=num(data, "message_wait", 0.0),
            multiplier=num(data, "multiplier", 1, kind=int, minimum=1),
            daily_limit=num(data, "daily_limit", 0, kind=int, minimum=0),
            stats_dir=text(data, "stats_dir"),
            resume=bool(data.get("resume", False)),
            listen=num(data, "listen", 0, kind=int, minimum=0),
            pictures=pictures,
            schedule_at=text(data, "schedule_at"),
            disable_link_preview=bool(data.get("disable_link_preview")),
            task_name=name_of(raw, data, "puppet-chat-v2"),
        )


def _as_list(value: object) -> list[str]:
    """One flag value -> a list, whether the YAML had a string or a list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).replace(",", "\n").splitlines()
            if part.strip()]


def startup_offsets(labels: list[str], interval: float) -> dict[str, float]:
    """Account ``i`` starts ``i * interval`` seconds after the first one."""
    return {label: index * max(0.0, interval)
            for index, label in enumerate(labels)}


def load_patterns(config: PuppetV2Config, base: Path) -> list[tuple[Path,
                                                                     list]]:
    """Every script of the task, main one first, loaded and checked.

    The main pattern still resolves through the mode file the way #0 does;
    the ``--patterns`` extras are taken as plain paths.
    """
    names = [config.pattern] + list(config.patterns)
    loaded: list[tuple[Path, list]] = []
    seen: set[str] = set()
    for position, name in enumerate(names):
        if not name or name in seen:
            continue
        seen.add(name)
        if position == 0:
            path = resolve_pattern(name, config.mode_file, config.mode, base)
        else:
            path = resolve_pattern(name, "", "", base)
        messages = chat_pattern.load(path)
        if not messages:
            raise ConfigError(f"{SECTION}: pattern has no lines: {path}")
        loaded.append((path, messages))
    if not loaded:
        raise ConfigError(f"{SECTION}: no script given (--pattern)")
    return loaded


def run(config: PuppetV2Config, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    labels = [account.label for account in accounts]
    base = Path(ctx.settings.data_dir)
    loaded = load_patterns(config, base)
    offsets = startup_offsets(labels, config.startup_interval)
    stats_dir = Path(config.stats_dir or base / STATS_DIR)
    slot_map = SlotMap(labels, daily_limit=config.daily_limit)

    per_cycle = len(config.targets) * sum(len(messages)
                                          for _path, messages in loaded) \
        * config.loops * config.multiplier
    total = per_cycle * config.cycles

    wait, when = schedule_wait(
        PuppetConfig(schedule_at=config.schedule_at), datetime.now(UTC))
    if wait:
        print(f"schedule: {when} (waiting {format_duration(wait)})")
        pause(ctx, wait)

    header(
        config.task_name,
        accounts=f"{len(accounts)}"
                 + (" (no account bound, plan only)" if not accounts else ""),
        targets=len(config.targets),
        script=loaded[0][0].name,
        patterns=len(loaded),
        lines=sum(len(messages) for _path, messages in loaded),
        cycles=config.cycles,
        cycle_interval=f"{config.cycle_interval:.0f}s",
        message_wait=f"{config.message_wait:.0f}s" if config.message_wait
        else "off",
        startup_interval=f"{config.startup_interval:.0f}s",
        loop_interval=f"{config.loop_interval:.0f} min",
        loops=config.loops,
        multiplier=config.multiplier,
        daily_limit=config.daily_limit or "-",
        resume="on" if config.resume else "off",
        listen=f"{config.listen}s" if config.listen else "off",
        schedule=config.schedule_at or "now",
        preview="off" if config.disable_link_preview else "on",
    )

    task = open_task(ctx, config.task_name, FEATURE,
                     {"patterns": [str(path) for path, _ in loaded],
                      "targets": config.targets, "cycles": config.cycles},
                     total)
    print(f"PuppetChatExecutor started - Task ID: {task.id}, "
          f"{len(config.targets)} groups, {config.cycles} cycles, "
          f"{len(loaded)} patterns")

    # ``--resume`` walks the receipts, not the stats file: the pairs the
    # feature already delivered as "sent" are skipped wherever they sit, the
    # way batch_join's restore works. The stats file records the cycles.
    done = frozenset(ctx.store.sent_pairs(FEATURE)) if config.resume \
        else frozenset()
    if config.resume and done:
        print(f"restore: {len(done)} already-sent pair(s) on file, "
              f"skipping them")

    ok_sum = failed_sum = skipped_sum = 0
    waited = 0.0
    index = 0
    for cycle in range(1, config.cycles + 1):
        if cycle > 1 and config.cycle_interval:
            waited += config.cycle_interval
            if ctx.live:
                time.sleep(config.cycle_interval)
            else:
                print(f"cycle {cycle}/{config.cycles} after "
                      f"{config.cycle_interval:.0f}s")
        print(f"CYCLE {cycle}/{config.cycles}")
        cycle_ok = cycle_failed = cycle_skipped = 0
        for path, messages in loaded:
            playback = PuppetConfig(
                account_labels=config.account_labels,
                targets=config.targets,
                pattern=str(path),
                mode_file="", mode="",
                loop_interval=config.loop_interval,
                loops=config.loops,
                multiplier=config.multiplier,
                daily_limit=config.daily_limit,
                polish=0, ai_polish=False,
                pictures=config.pictures,
                disable_link_preview=config.disable_link_preview,
                task_name=config.task_name,
            )
            outcome = play(ctx, task, messages, playback, labels,
                           startup_offsets=offsets, slot_map=slot_map,
                           resume_pairs=done, index_base=index,
                           message_wait=config.message_wait,
                           total_override=total)
            index += outcome.ok + outcome.failed + outcome.skipped
            cycle_ok += outcome.ok
            cycle_failed += outcome.failed
            cycle_skipped += outcome.skipped
            waited += outcome.waited
        ok_sum += cycle_ok
        failed_sum += cycle_failed
        skipped_sum += cycle_skipped
        entry = StatsFile(task=config.task_name, cycle=cycle, ok=cycle_ok,
                          failed=cycle_failed, skipped=cycle_skipped)
        written = entry.dump(stats_dir)
        print(f"stats: {written.name} cycle={cycle} ok={cycle_ok} "
              f"failed={cycle_failed} skipped={cycle_skipped}")

    heard = 0
    if config.listen:
        heard = _listen(config, ctx, labels, config.targets, config.listen)

    print(f"Processed: {ok_sum + failed_sum + skipped_sum} / {total}")
    print(f"ok: {ok_sum}")
    print(f"failed: {failed_sum}")
    print(f"skipped: {skipped_sum}")
    print(f"started accounts: {', '.join(labels) or '-'}")
    if config.listen:
        print(f"speech: heard {heard} message(s)")
    print(f"PLAN: {total} messages, {max(1, len(labels))} accounts, "
          f"ETA {format_duration(waited)}")
    close_task(ctx, task, ok=ok_sum, failed=failed_sum)
    return 0 if not failed_sum else 1


def _listen(config: PuppetV2Config, ctx: RunContext, labels: list[str],
            targets: list[str], seconds: int) -> int:
    """Keep the speech log open for ``seconds``; return what was heard.

    ``handleReceivedMessage`` / ``speechLogged``: after the puppet lines are
    done, every bound account watches the own groups and every arriving
    message is logged. Messages the bound accounts themselves sent are
    dropped (``Filter messages from internal puppet accounts``).
    """
    try:
        from telethon import events
    except ImportError:  # pragma: no cover - live only
        print("ERROR: telethon is not installed")
        return 0

    sender = ctx.sender()
    clients: list[tuple[str, object]] = []
    own: dict[int, int | None] = {}
    for label in labels or []:
        try:
            client = sender._client(label)  # noqa: SLF001
        except Exception as error:  # noqa: BLE001 - reported and skipped
            ctx.note(f"{label}: {error}", "warn")
            continue
        clients.append((label, client))
        try:
            me = client.get_me()
            own[id(client)] = getattr(me, "id", None)
        except Exception:  # noqa: BLE001 - the filter just stays off
            own[id(client)] = None
    if not clients:
        print("listen: no account could be opened, speech window skipped")
        return 0

    owner = {id(client): label for label, client in clients}
    heard = 0

    @events.register(events.NewMessage(incoming=True))
    async def handler(event):  # noqa: ANN001, ANN202 - Telethon callback
        nonlocal heard
        client = getattr(event, "client", None)
        sender_id = getattr(getattr(event, "message", None), "sender_id",
                            None) or getattr(event, "sender_id", None)
        if sender_id and sender_id == own.get(id(client)):
            print(f"speech: dropped internal puppet message "
                  f"(sender {sender_id})")
            return
        heard += 1
        body = (getattr(event, "raw_text", "") or "").replace("\n", " ")
        print(f"speech: account={owner.get(id(client), '-')} "
              f"peer={getattr(event, 'chat_id', '')} sender={sender_id} "
              f"text={body}")

    for _label, client in clients:
        client.add_event_handler(handler)
    print(f"listen: {len(clients)} account(s) logging speech for "
          f"{seconds}s, Ctrl+C to stop early")
    import asyncio
    import contextlib

    loop = asyncio.get_event_loop()
    loop.call_later(seconds, lambda: loop.call_soon_threadsafe(loop.stop))
    try:
        loop.run_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        ctx.note("speech window interrupted by the operator", "warn")
    finally:
        for _label, client in clients:
            with contextlib.suppress(Exception):
                client.remove_event_handler(handler)
    return heard


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(PuppetV2Config.from_dict(raw, ctx.settings), ctx)


__all__ = ["PuppetV2Config", "STATS_DIR", "SlotMap", "TITLE", "main", "run",
           "startup_offsets"]
