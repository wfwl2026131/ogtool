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
    num,
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
    # --- batch_login_config.json, by the page's own key names -------------
    #: ``qrTimeoutSec`` / ``smsTimeoutSec``: how long each code path waits.
    qr_timeout_sec: int = 120
    sms_timeout_sec: int = 120
    #: ``loginBaseApi``: the panel endpoint; must be http(s), per the page.
    login_base_api: str = ""
    #: ``twoFactorPassword`` / ``storedLoginTokens``: secrets, never printed.
    two_factor_password: str = ""
    #: ``dynamicProxyEnabled`` / ``dynamicProxy``: ip:port:user:pass, with
    #: ``{SessionKey}`` expanded per account the way the page does.
    dynamic_proxy_enabled: bool = False
    dynamic_proxy: str = ""
    #: ``hideImportDialog`` / ``importSessionOnDrop`` / ``collectNestedSessionFiles``
    hide_import_dialog: bool = False
    import_session_on_drop: bool = True
    collect_nested_session_files: bool = True
    #: ``loginMethod`` / ``loginMode`` / ``maxConcurrent``.
    login_method: str = "session"
    login_mode: str = "silent"
    max_concurrent: int = 1
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
        base_api = text(data, "login_base_api")
        if base_api and not base_api.startswith(("http://", "https://")):
            # The page refuses this itself ("Login Base API must start with
            # http:// or https://"), so the same sentence is the error here.
            raise ConfigError(
                f"{SECTION}: login_base_api must start with http:// or "
                f"https://, is {base_api}")
        proxy = text(data, "dynamic_proxy")
        proxy_on = bool(data.get("dynamic_proxy_enabled", False))
        if proxy_on and not proxy:
            # "Dynamic proxy is enabled but empty." is the page's own wording.
            raise ConfigError(
                f"{SECTION}: dynamic proxy is enabled but dynamic_proxy is "
                f"empty")
        if proxy:
            _check_proxy(proxy)
        return cls(
            account_labels=accounts_of(raw, data),
            import_folder=folder,
            import_zip=archive,
            export_ids=text(data, "export_ids"),
            logout=text(data, "logout"),
            label_prefix=text(data, "label_prefix"),
            limit=num_limit(data),
            qr_timeout_sec=num(data, "qr_timeout_sec", 120, kind=int,
                               minimum=1),
            sms_timeout_sec=num(data, "sms_timeout_sec", 120, kind=int,
                                minimum=1),
            login_base_api=base_api,
            two_factor_password=str(data.get("two_factor_password") or ""),
            dynamic_proxy_enabled=proxy_on,
            dynamic_proxy=proxy,
            hide_import_dialog=bool(data.get("hide_import_dialog", False)),
            import_session_on_drop=bool(
                data.get("import_session_on_drop", True)),
            collect_nested_session_files=bool(
                data.get("collect_nested_session_files", True)),
            login_method=str(data.get("login_method") or "session"),
            login_mode=str(data.get("login_mode") or "silent"),
            max_concurrent=num(data, "max_concurrent", 1, kind=int,
                               minimum=1),
            task_name=name_of(raw, data, "batch-login"),
        )


PROXY_PARTS = 4


def _check_proxy(value: str) -> None:
    """``ip:port:username:password``, with ``{SessionKey}`` allowed anywhere.

    The page's own message ("Invalid proxy format. Expected
    ip:port:username:password.") is the error text: a proxy that silently does
    nothing is a login that silently fails.
    """
    if value.count(":") < PROXY_PARTS - 1:
        raise ConfigError(
            f"{SECTION}: invalid proxy format, expected "
            f"ip:port:username:password, got {value}")
    host, port = value.split(":")[:2]
    if not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ConfigError(
            f"{SECTION}: invalid proxy host or port in {value}")


def proxy_for(config: LoginConfig, label: str) -> str:
    """The proxy line for one account, with ``{SessionKey}`` filled in.

    The page supports a dynamic proxy whose user name carries a random key
    (``{SessionKey}`` / ``{SessionKey#12}`` / ``{SessionKey#d#12}``); the key is
    derived from the account label so a dry run is reproducible.
    """
    if not config.dynamic_proxy:
        return ""
    import hashlib

    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()
    value = config.dynamic_proxy
    if "{SessionKey#d#12}" in value:
        value = value.replace("{SessionKey#d#12}",
                              "".join(char for char in digest if char.isdigit())[:12])
    if "{SessionKey#12}" in value:
        value = value.replace("{SessionKey#12}", digest[:12])
    if "{SessionKey}" in value:
        value = value.replace("{SessionKey}", _alnum_key(digest[:8]))
    return value


def _alnum_key(source: str) -> str:
    """Eight alphanumerics: the page's plain ``{SessionKey}`` form."""
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(alphabet[int(char, 16)] for char in source)


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
    print(f"login method: {config.login_method}  mode: {config.login_mode}")
    print(f"timeouts: qr {config.qr_timeout_sec}s  sms {config.sms_timeout_sec}s")
    if config.login_base_api:
        print(f"login base api: {config.login_base_api}")
    if config.dynamic_proxy_enabled:
        # The proxy line carries a password, so only its shape is printed.
        host, port = config.dynamic_proxy.split(":")[:2]
        print(f"dynamic proxy: {host}:{port} "
              f"(session key: {'yes' if '{SessionKey' in config.dynamic_proxy else 'no'})")
    print(f"two-factor password: "
          f"{'set' if config.two_factor_password else 'not set'}")
    print(f"max concurrent: {config.max_concurrent}  "
          f"nested folders: {'yes' if config.collect_nested_session_files else 'no'}")
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


#: Which recorded kind each mode logs out.  ``all`` is the only mode that
#: ignores the column: it means every account, classified or not.
MODE_KIND = {LOGOUT_FROZEN: "frozen", LOGOUT_BOTH: "two_way"}


def _logout(mode: str, ctx: RunContext) -> int:
    """The three bulk logouts: printed in a dry run, performed when live.

    ``frozen`` and ``both`` pick accounts whose recorded kind matches; an
    account nobody classified is skipped and counted, never quietly swept into
    a mode it does not belong to.  ``accounts kind --label X --kind frozen``
    is how the pool learns the distinction.
    """
    accounts = ctx.store.list_accounts()
    if not accounts:
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
    wanted = MODE_KIND.get(mode)
    if wanted:
        labels = [a.label for a in accounts if a.kind == wanted]
        skipped = [a.label for a in accounts if a.kind != wanted]
    else:
        labels = [a.label for a in accounts]
        skipped = []
    if wanted and skipped:
        detail = ", ".join(f"{a.label}={a.kind or '-'}"
                           for a in accounts if a.kind != wanted)
        print(f"{plan[mode]}：跳过 {len(skipped)} 个非本类别（{detail}）")
    if wanted and not labels:
        print(f"批量退出{plan[mode]}：0 accounts")
        print("nothing to log out for this mode")
        return 0
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
