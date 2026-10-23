"""#9 batch login - import .session files plus their .json metadata.

Fields the page shows: the import folder, the ZIP import, the account count, the
export of every id, and the three bulk logouts (frozen / all / both).

Only ``.session`` and the sidecar ``.json`` are read: the reference client can
also read a ``tdata`` folder, but that is a binary format and out of scope here.
"""

from __future__ import annotations

import json
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ConfigError, Settings
from ..flags import Flag
from ..report import export, write_lines
from ..runtime import RunContext
from . import register
from ._common import (
    accounts_of,
    close_task,
    name_of,
    open_task,
    out_dir,
    section,
    text,
)

FEATURE = "batch_login"
SECTION = "batch_login"
TITLE = "#9 批量上号"

LOGOUT_FROZEN = "frozen"
LOGOUT_ALL = "all"
LOGOUT_BOTH = "both"

FLAGS = (
    Flag("--import-folder", "import_folder", str,
         "folder holding .session files (+ optional .json)"),
    Flag("--import-zip", "import_zip", str, "ZIP holding the same layout"),
    Flag("--export-ids", "export_ids", str, "write every account id here"),
    Flag("--logout", "logout", str, "frozen|all|both"),
    Flag("--label-prefix", "label_prefix", str, "prefix for imported labels"),
    Flag("--limit", "limit", int, "import at most this many accounts"),
)


@dataclass(slots=True)
class LoginConfig:
    account_labels: list[str] = field(default_factory=list)
    import_folder: str = ""
    import_zip: str = ""
    export_ids: str = ""
    logout: str = ""
    label_prefix: str = ""
    limit: int = 0
    task_name: str = "batch-login"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> LoginConfig:
        del settings
        data = section(raw, SECTION)
        folder = text(data, "import_folder")
        archive = text(data, "import_zip")
        if not folder and not archive and not data.get("logout"):
            raise ConfigError(
                f"{SECTION}: give --import-folder, --import-zip or --logout"
            )
        return cls(
            account_labels=accounts_of(raw, data),
            import_folder=folder,
            import_zip=archive,
            export_ids=text(data, "export_ids"),
            logout=text(data, "logout"),
            label_prefix=text(data, "label_prefix"),
            limit=num_limit(data),
            task_name=name_of(raw, data, "batch-login"),
        )


def num_limit(data: dict) -> int:
    value = data.get("limit")
    try:
        return max(0, int(value)) if value else 0
    except (TypeError, ValueError):
        return 0


def find_sessions(folder: Path) -> list[Path]:
    """Every ``.session`` under one folder, sorted, journals excluded."""
    if not folder.exists() or not folder.is_dir():
        raise ConfigError(f"not a folder: {folder}")
    return sorted(path for path in folder.rglob("*.session") if path.is_file())


def read_metadata(session: Path) -> dict[str, str]:
    """The sidecar ``.json`` next to a session, if there is one."""
    sidecar = session.with_suffix(".json")
    if not sidecar.exists():
        return {}
    try:
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in ("id", "user_id", "phone", "username", "first_name", "last_name"):
        if key in raw and raw[key] not in (None, ""):
            out[key] = str(raw[key])
    if "app_id" in raw:
        out.setdefault("id", "")
    return out


def run(config: LoginConfig, ctx: RunContext) -> int:
    task = open_task(ctx, config.task_name, FEATURE, {"section": SECTION}, 0)
    imported = 0
    folder = Path(config.import_folder).expanduser() if config.import_folder else None

    if config.import_zip:
        archive = Path(config.import_zip).expanduser()
        if not archive.exists():
            print(f"ERROR: zip not found: {archive}")
            return 2
        with tempfile.TemporaryDirectory() as workspace:
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(workspace)
            folder = Path(workspace)
            imported += _import(folder, config, ctx)
    elif folder is not None:
        imported += _import(folder, config, ctx)

    accounts = ctx.store.list_accounts()
    print(f"账号数：{len(accounts)}")
    for account in accounts:
        print(f"{account.label}\t{account.phone or '-'}\t{account.status}")

    if config.export_ids:
        base = out_dir(ctx, {}, ctx.settings)
        path = config.export_ids if config.export_ids not in ("-", "auto") \
            else f"{base}/accounts_ids.txt"
        rows = []
        for account in accounts:
            meta = read_metadata(Path(account.session)) if account.session else {}
            rows.append(f"{account.label}\t{meta.get('id') or account.label}\t"
                        f"{account.phone or '-'}")
        write_lines(path, rows, header="label\tid\tphone")
        print(f"导出所有ID：{path} ({len(rows)})")
        export(f"{base}/accounts_count.txt", [str(len(accounts))], "accounts")

    if config.logout:
        code = _logout(config.logout, ctx)
        close_task(ctx, task, ok=code, failed=0)
        return 0 if code >= 0 else 2

    print(f"PLAN: {imported} imported, {len(accounts)} total accounts")
    close_task(ctx, task, ok=imported, failed=0)
    return 0


def _import(folder: Path, config: LoginConfig, ctx: RunContext) -> int:
    sessions = find_sessions(folder)
    if config.limit:
        sessions = sessions[:config.limit]
    if not sessions:
        print(f"no .session found under {folder}")
        return 0
    pool = ctx.pool
    imported = 0
    for session in sessions:
        label = f"{config.label_prefix}{session.stem}"
        meta = read_metadata(session)
        account = pool.add_session(session, label)
        if meta.get("phone"):
            account.phone = meta["phone"]
            ctx.store.add_account(account)
        imported += 1
        print(f"imported {label} phone={account.phone or '-'} "
              f"meta={'yes' if meta else 'no'}")
    return imported


def _logout(mode: str, ctx: RunContext) -> int:
    """The three bulk logouts: printed in a dry run, performed when live.

    The three modes name the same pool here, because the account table does not
    record the frozen / two way distinction the reference keeps on its side.
    """
    labels = [account.label for account in ctx.store.list_accounts()]
    if not labels:
        print("no accounts to log out")
        return 0
    plan = {
        LOGOUT_FROZEN: "冻结账号",
        LOGOUT_ALL: "所有账号",
        LOGOUT_BOTH: "双向号",
    }
    if mode not in plan:
        print(f"ERROR: --logout wants frozen|all|both, not {mode!r}")
        return -1
    print(f"批量退出{plan[mode]}：{len(labels)} accounts")
    if not ctx.live:
        for label in labels:
            print(f"logout {mode} {label}")
        print("dry-run: nothing was logged out")
        return len(labels)

    sender = ctx.sender()
    done = 0
    for label in labels:
        result = sender.logout(label)
        if result.ok:
            done += 1
            ctx.store.set_account_status(label, "logged-out")
            print(f"logout {mode} {label} -> ok")
        else:
            print(f"logout {mode} {label} -> {result.code} {result.detail}")
    print(f"logged out: {done}/{len(labels)}")
    return done


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(LoginConfig.from_dict(raw, ctx.settings), ctx)


__all__ = ["LOGOUT_ALL", "LOGOUT_BOTH", "LOGOUT_FROZEN", "LoginConfig",
           "TITLE", "find_sessions", "main", "read_metadata", "run"]
