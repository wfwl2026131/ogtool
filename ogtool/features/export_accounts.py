"""#22 advanced - export the accounts the tool holds.

The reference page says "export your Officegram sessions as standard Telegram
format" and offers an account picker plus an output folder.  This module does
the part that can be done honestly without writing a binary TDATA store: a
report of what each account is (id, username, masked phone, device, 2FA state,
session path, last activity) in CSV and JSON, next to a copy of the session
files.

The TDATA conversion itself is **not implemented**; see :data:`NOT_IMPLEMENTED`
and the note the run prints, so nobody mistakes the report for a portable
session.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ConfigError, Settings
from ..flags import Flag
from ..runtime import RunContext
from ..store import Account, Profile
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    name_of,
    open_task,
    out_dir,
    record,
    section,
    text,
)

FEATURE = "export_accounts"
SECTION = "export_accounts"
TITLE = "#22 高级功能"

NOT_IMPLEMENTED = (
    "not implemented: turning a .session into a standard Telegram tdata store "
    "(that needs the binary format and is out of scope for the script version)"
)

FIELDS = ("label", "user_id", "username", "phone", "device", "twofa",
          "session", "last_active")

FLAGS = (
    Flag("--output-dir", "output_dir", str, "where the export goes"),
    Flag("--profile", "profile", str,
         "simulated profile: label|id|username|device|2fa (repeatable)",
         action="append"),
    Flag("--no-sessions", "no_sessions", str, "skip the session file copy",
         action="store_true"),
    Flag("--format", "formats", str, "csv, json or both"),
)


@dataclass(slots=True)
class ExportConfig:
    account_labels: list[str] = field(default_factory=list)
    output_dir: str = ""
    profiles: dict[str, Profile] = field(default_factory=dict)
    copy_sessions: bool = True
    formats: tuple[str, ...] = ("csv", "json")
    task_name: str = "export-accounts"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> ExportConfig:
        del settings
        data = section(raw, SECTION)
        formats = text(data, "formats") or "csv,json"
        wanted = tuple(part.strip().lower()
                       for part in formats.replace(" ", ",").split(",")
                       if part.strip())
        unknown = [item for item in wanted if item not in ("csv", "json")]
        if unknown:
            raise ConfigError(
                f"{SECTION}: unknown export format: {', '.join(unknown)}"
            )
        return cls(
            account_labels=accounts_of(raw, data),
            output_dir=text(data, "output_dir"),
            profiles=parse_profiles(_as_list(data.get("profile"))),
            copy_sessions=not bool(data.get("no_sessions")),
            formats=wanted or ("csv", "json"),
            task_name=name_of(raw, data, "export-accounts"),
        )


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [part.strip() for part in str(value).replace(",", "\n").splitlines()
            if part.strip()]


def parse_profiles(values: list[str]) -> dict[str, Profile]:
    """``label|id|username|device|2fa`` lines a dry run can use as its own data."""
    out: dict[str, Profile] = {}
    for entry in values:
        parts = [part.strip() for part in str(entry).split("|")]
        if not parts or not parts[0]:
            continue
        while len(parts) < 5:
            parts.append("")
        out[parts[0]] = Profile(
            label=parts[0], user_id=parts[1], username=parts[2],
            device=parts[3], twofa=parts[4],
        )
    return out


def build_rows(accounts: list[Account], store_profiles: list[Profile],
               simulated: dict[str, Profile]) -> list[dict[str, str]]:
    """One row per account, merging the pool, the stored profile and the input."""
    by_label = {item.label: item for item in store_profiles}
    rows: list[dict[str, str]] = []
    for account in accounts:
        profile = simulated.get(account.label) or by_label.get(account.label)
        phone = (profile.phone if profile and profile.phone else account.phone)
        row = {
            "label": account.label,
            "user_id": (profile.user_id if profile else "") or "-",
            "username": (profile.username if profile else "") or "-",
            "phone": Profile(account.label, phone=phone).masked_phone(),
            "device": (profile.device if profile else "") or "-",
            "twofa": (profile.twofa if profile else "") or "-",
            "session": account.session or "-",
            "last_active": (profile.last_active if profile else "") or "-",
        }
        rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(FIELDS)]
    for row in rows:
        lines.append(",".join(_quote(row.get(field, "")) for field in FIELDS))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)


def write_json(path: Path, rows: list[dict[str, str]],
               extra: dict[str, object] | None = None) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"accounts": rows}
    payload.update(extra or {})
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return len(rows)


def _quote(value: str) -> str:
    text_value = str(value or "")
    if any(char in text_value for char in (",", '"', "\n")):
        return '"' + text_value.replace('"', '""') + '"'
    return text_value


def archive_sessions(accounts: list[Account], target: Path) -> list[str]:
    """Copy each session file next to the report; a missing one is reported."""
    target.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for account in accounts:
        if not account.session:
            continue
        source = Path(account.session).expanduser()
        if not source.exists():
            print(f"session: {account.label} -> missing ({source})")
            continue
        destination = target / source.name
        shutil.copy2(source, destination)
        copied.append(str(destination))
    return copied


def run(config: ExportConfig, ctx: RunContext) -> int:
    accounts = ctx.bind(config.account_labels)
    label_set = {account.label for account in accounts}
    stored = [item for item in ctx.store.list_profiles()
              if not label_set or item.label in label_set]
    rows = build_rows(accounts, stored, config.profiles)

    base = Path(out_dir(ctx, {"output_dir": config.output_dir},
                        ctx.settings))
    header(
        config.task_name,
        accounts=len(accounts),
        output=base,
        formats=", ".join(config.formats),
        sessions="copy" if config.copy_sessions else "skip",
        masked_phones="yes",
    )

    # Keyword arguments, and through the shared helper: ``len(rows)`` in the
    # mode slot is how a receipt ends up saying mode "2" with planned 0.
    task = open_task(ctx, config.task_name, FEATURE,
                     {"accounts": sorted(label_set), "formats": config.formats,
                      "copy_sessions": config.copy_sessions}, len(rows))

    written: list[str] = []
    if "csv" in config.formats:
        path = base / "accounts.csv"
        write_csv(path, rows)
        written.append(str(path))
        print(f"exported: {path} ({len(rows)} rows)")
    if "json" in config.formats:
        path = base / "accounts.json"
        write_json(path, rows, {"not_implemented": NOT_IMPLEMENTED})
        written.append(str(path))
        print(f"exported: {path} ({len(rows)} rows)")
    if config.copy_sessions and accounts:
        copied = archive_sessions(accounts, base / "sessions")
        print(f"archived: {len(copied)} session files -> {base / 'sessions'}")
    for row in rows:
        print(f"  {row['label']}\t{row['user_id']}\t{row['phone']}\t"
              f"{row['device']}")

    print(f"PLAN: {len(rows)} accounts, {len(written)} files, export {base}")
    print(f"note: {NOT_IMPLEMENTED}")
    # A row per exported account, for the same reason keep_online writes one:
    # ok=len(rows) with an empty results table is a count with nothing behind it.
    for row in rows:
        record(ctx, task, str(row.get("label", "")), "export", True)
    close_task(ctx, task, ok=len(rows), failed=0)
    return 0


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(ExportConfig.from_dict(raw, ctx.settings), ctx)


__all__ = [
    "ExportConfig",
    "FIELDS",
    "NOT_IMPLEMENTED",
    "TITLE",
    "archive_sessions",
    "build_rows",
    "main",
    "parse_profiles",
    "run",
    "write_csv",
    "write_json",
]
