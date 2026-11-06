"""#T4 the Client List - the CRM page of the right toolbar.

The reference keeps one ``client`` table (``Deep::ClientRecord``,
:1737164) with a phone key, a username, an add counter and the failed
counter, and the page drives it with CSV import/export, the delete
forms, search, the worker-account selector and the conversation opener
(:1741630-1741711).  This module is that page, CLI-shaped: the store
holds the rows, every verdict below is the page's own sentence, and a
dry run plans instead of opening conversations.

``Encrypt`` / ``Import`` (:1741634-1741635) carry the file pair: a CSV
import fills the table (``Import successful: %1`` / ``Import failed``),
and an encrypted export wraps the same CSV in the page's ``.dm`` form
(:1741699) - the wrapper is a XOR-keystream of the page's own magic
(:1741701-1741704), which this build reproduces honestly rather than
guessing: the header is written with the page's magic numbers and the
body stays plain, so the reference's own reader would refuse it
(``Failed to decrypt file``) instead of silently accepting a forgery.
"""

from __future__ import annotations

import base64
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..flags import Flag
from ..runtime import RunContext
from ..store import Store
from . import register
from ._common import (
    accounts_of,
    close_task,
    header,
    name_of,
    open_task,
    section,
    text,
)

FEATURE = "client_list"
SECTION = "client_list"
TITLE = "#T4 客户列表"

FLAGS = (
    Flag("--import-csv", "import_csv", str, "CSV file with Phone/Username"),
    Flag("--export-csv", "export_csv", str, "write the table to a CSV file"),
    Flag("--encrypt", "encrypt", str, "wrap an exported CSV in the .dm form"),
    Flag("--decrypt", "decrypt", str, "read a .dm file back to CSV"),
    Flag(
        "--delete",
        "delete",
        str,
        "phones/usernames to delete, comma separated (repeatable)",
        action="append",
    ),
    Flag("--search", "search", str, "phone/username substring"),
    Flag("--client", "client", str, "one client's phone for a count action"),
    Flag(
        "--count-success/--count-failed",
        "count_success",
        None,
        "bump the add counter or the failed one",
        action="bool_pair",
    ),
    Flag(
        "--show-worker",
        "show_worker",
        None,
        "the worker-account selector for --client",
        action="store_true",
    ),
    Flag(
        "--open-conversation",
        "open_conversation",
        None,
        "open the chat with --client (live)",
        action="store_true",
    ),
)

#: The page's error-class map (:1741663-1741687): the transport's refusal
#: becomes the sentence the page shows.
ERROR_WORDS = (
    ("PHONE_NUMBER_INVALID", "Invalid phone number"),
    ("PHONE_NUMBER_UNOCCUPIED", "Phone number is not registered"),
    ("ACCESS_DENIED", "Access denied"),
    ("FLOOD_WAIT", "Rate limit exceeded, please wait and try again"),
    ("TOO_MANY_REQUESTS", "Too many requests, please try again later"),
    ("TIMEOUT", "Request timeout, please try again"),
)


@dataclass(slots=True)
class ClientListConfig:
    account_labels: list[str] = field(default_factory=list)
    import_csv: str = ""
    export_csv: str = ""
    encrypt: str = ""
    decrypt: str = ""
    delete: list[str] = field(default_factory=list)
    search: str = ""
    client: str = ""
    count_success: bool | None = None
    show_worker: bool = False
    open_conversation: bool = False
    task_name: str = "client-list"

    @classmethod
    def from_dict(cls, raw: dict, settings: Settings) -> ClientListConfig:
        data = section(raw, SECTION)
        return cls(
            account_labels=accounts_of(raw, data),
            import_csv=text(data, "import_csv"),
            export_csv=text(data, "export_csv"),
            encrypt=text(data, "encrypt"),
            decrypt=text(data, "decrypt"),
            delete=[str(item) for item in (data.get("delete") or [])],
            search=text(data, "search"),
            client=text(data, "client"),
            count_success=(
                bool(data["count_success"])
                if data.get("count_success") is not None
                else None
            ),
            show_worker=bool(data.get("show_worker", False)),
            open_conversation=bool(data.get("open_conversation", False)),
            task_name=name_of(raw, data, "client-list"),
        )


def _phones_and_usernames(tokens: list[str]) -> tuple[list[str], list[str]]:
    """``+86...`` and digits are phones; ``@name`` and words are usernames."""
    phones = [item for item in tokens if item.lstrip("+").isdigit()]
    usernames = [item for item in tokens if item not in phones]
    return phones, usernames


def read_clients_csv(path: str | Path) -> list[dict[str, str]]:
    """The import file's rows, keyed as the page's columns are."""
    rows: list[dict[str, str]] = []
    with Path(path).expanduser().open(newline="", encoding="utf-8-sig") as handle:
        for raw_row in csv.DictReader(handle):
            row = {
                (key or "").strip().lower(): (value or "").strip()
                for key, value in raw_row.items()
            }
            rows.append(row)
    return rows


def import_csv(store: Store, path: str | Path) -> int:
    """The page's import: one row per CSV line, verdicts are its own."""
    try:
        rows = read_clients_csv(path)
    except OSError as error:
        print("Import failed")
        print(f"ClientListPage: Error showing worker account selector: {error}")
        return 0
    added = 0
    for row in rows:
        phone = row.get("phone", "")
        if not phone:
            continue
        store.upsert_client(
            phone,
            username=row.get("username", ""),
            reserve2=row.get("reserve2", ""),
            reserve3=row.get("reserve3", ""),
            reserve4=row.get("reserve4", ""),
            reserve6=row.get("reserve6", ""),
        )
        added += 1
    if added:
        print(f"Import successful: {added}")
    else:
        print("Import failed")
    return added


def export_csv(store: Store, path: str | Path) -> int:
    """The same table as a CSV file; the page names the file dialog (:1741695)."""
    rows = store.client_rows()
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Phone", "Username", "AddCount", "FailCount"])
        for row in rows:
            writer.writerow(
                [
                    row["phone"],
                    row["username"],
                    row["add_count"],
                    row["fail_count"],
                ]
            )
    print(f"Import successful: {len(rows)}")
    return len(rows)


def encrypt_file(path: str | Path) -> str | None:
    """The page's ``.dm`` wrapper: its magic header, honest plain body.

    :1741697-1741704: a failure names itself; a success names the file.
    The wrapper is the page's own format marker, not a cipher - this
    build does not possess the reference's key schedule, so the body is
    the plain CSV and the reader is told so by the ``kind`` field.
    """
    source = Path(path).expanduser()
    if not source.is_file():
        print("Failed to encrypt file")
        return None
    target = source.with_suffix(".dm")
    payload = {
        "kind": "client_list_plain",
        "magic": ["10016", "10018", "NXM_10012"],
        "body": base64.b64encode(source.read_bytes()).decode("ascii"),
    }
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Encrypted file saved to: {target}")
    return str(target)


def decrypt_file(path: str | Path) -> str | None:
    """Read a ``.dm`` wrapper back to the CSV it carries (:1741699)."""
    source = Path(path).expanduser()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("Failed to decrypt file")
        return None
    if not isinstance(payload, dict) or "body" not in payload:
        print("Failed to decrypt file")
        return None
    target = source.with_suffix(".csv")
    target.write_bytes(base64.b64decode(payload["body"]))
    print(f"Import successful: {target}")
    return str(target)


def error_word(code: str) -> str:
    """The page's sentence for a transport refusal, or the generic one."""
    for prefix, sentence in ERROR_WORDS:
        if prefix.casefold() in code.casefold():
            return sentence
    return "Unknown error occurred"


def run(config: ClientListConfig, ctx: RunContext) -> int:
    store = Store(ctx.settings.db_path)
    header(
        TITLE,
        accounts=f"{len(config.account_labels)}"
        + (" (no account bound, plan only)" if not config.account_labels else ""),
        import_=config.import_csv or "-",
        export=config.export_csv or "-",
        search=config.search or "-",
        client=config.client or "-",
    )
    task = open_task(
        ctx,
        config.task_name,
        FEATURE,
        {"import": config.import_csv, "export": config.export_csv},
        planned=0,
    )
    failed = 0
    if config.import_csv:
        failed += 0 if import_csv(store, config.import_csv) else 1
    if config.export_csv:
        failed += 0 if export_csv(store, config.export_csv) else 1
    if config.encrypt:
        failed += 0 if encrypt_file(config.encrypt) else 1
    if config.decrypt:
        failed += 0 if decrypt_file(config.decrypt) else 1
    if config.delete:
        phones, usernames = _phones_and_usernames(
            [item.strip() for item in config.delete if item.strip()]
        )
        removed = store.delete_clients(phones, usernames)
        if removed:
            print(f"Delete successful: {removed}")
        else:
            print("Delete failed")
            failed += 1
    rows = store.client_rows(config.search)
    if config.count_success is not None and config.client:
        count = store.bump_client_counter(config.client, success=config.count_success)
        if config.count_success:
            print(
                f"ClientListPage: Clicked success count area - Client: "
                f"{config.client}, Add Count: {count} sucList"
            )
        else:
            print(
                f"ClientListPage: Clicked failed count area - Client: "
                f"{config.client}, Failed Count: {count}"
            )
    if config.show_worker:
        if config.client and store.client_get(config.client) is None:
            print("ClientListPage: No worker account IDs found for client:")
            print("No worker account IDs found for client")
        else:
            print(
                f"ClientListPage: Showing worker account selector - Client: "
                f"{config.client or '-'}, Account Count: "
                f"{len(config.account_labels)}"
            )
    if config.open_conversation:
        if not ctx.live:
            print("dry-run: no conversation was opened")
        else:
            sender = ctx.sender()
            from ..targets import parse_many

            target = parse_many([config.client], "custom")[0]
            outcome = sender.resolve(
                config.account_labels[0] if config.account_labels else "-", target
            )
            if outcome.ok:
                print("Conversation opened")
                print(f" - PeerID: {outcome.detail}")
            else:
                print(f"Failed to open conversation: {error_word(outcome.code)}")
                failed += 1
    for row in rows[:50]:
        print(
            f"{row['phone']} {row['username'] or '-'} "
            f"add={row['add_count']} fail={row['fail_count']}"
        )
    print(f"client: {len(rows)} row(s)")
    close_task(ctx, task, ok=len(rows), failed=failed)
    return 0 if not failed else 1


@register(FEATURE)
def main(raw: dict, ctx: RunContext) -> int:
    return run(ClientListConfig.from_dict(raw, ctx.settings), ctx)


__all__ = [
    "FEATURE",
    "ClientListConfig",
    "ERROR_WORDS",
    "decrypt_file",
    "encrypt_file",
    "error_word",
    "export_csv",
    "import_csv",
    "read_clients_csv",
    "run",
]
