"""The account pool: sessions on disk plus the rows that describe them."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import ConfigError, Settings
from .senders import SESSION_NOT_FOUND
from .store import Account, Store


class AccountError(Exception):
    """The pool cannot satisfy the request."""


def label_for(phone: str) -> str:
    """``+86 138-0000-0000`` -> ``8613800000000``."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not digits:
        raise AccountError(f"not a phone number: {phone}")
    return digits


@dataclass(slots=True)
class AccountPool:
    settings: Settings
    store: Store

    def add_phone(self, phone: str, label: str = "", notes: str = "") -> Account:
        name = label or label_for(phone)
        session = self.settings.session_path(name)
        account = Account(
            label=name,
            phone=phone,
            session=str(session),
            proxy=_proxy_row(self.settings),
            status="new",
            notes=notes,
        )
        return self.store.add_account(account)

    def add_session(self, path: str | Path, label: str = "") -> Account:
        source = Path(path).expanduser()
        if not source.exists():
            raise AccountError(f"session file not found: {source}")
        name = label or source.stem
        target = self.settings.session_path(name)
        if source.resolve() != target.resolve():
            target.write_bytes(source.read_bytes())
        account = Account(
            label=name,
            session=str(target),
            proxy=_proxy_row(self.settings),
            status="imported",
        )
        return self.store.add_account(account)

    def list(self) -> list[Account]:
        """The whole pool, read in the batches the settings page asks for.

        ``account_batch`` only decides how many rows are pulled at a time, so a
        pool with thousands of rows does not land in memory in one go; the
        order is unchanged.
        """
        batch = max(1, self.settings.runtime.account_batch)
        rows: list[Account] = []
        for offset in range(0, self.store.count_accounts(), batch):
            rows.extend(self.store.list_accounts(limit=batch, offset=offset))
        return rows

    def resolve(self, names: list[str] | None) -> list[Account]:
        """The accounts a task runs with: the given ones or the whole pool."""
        accounts = self.list()
        if not names:
            return accounts
        by_label = {account.label: account for account in accounts}
        chosen: list[Account] = []
        for name in names:
            account = (
                by_label.get(name) or by_label.get(label_for(name))
                if name.replace("+", "").isdigit()
                else by_label.get(name)
            )
            if account is None:
                raise AccountError(f"{SESSION_NOT_FOUND}: {name}")
            chosen.append(account)
        return chosen

    def test(
        self, names: list[str] | None, live: bool, client_factory=None
    ) -> list[tuple[str, bool, str]]:
        """Connectivity check. In dry-run it only proves the file is there.

        Live means a real ``get_me()``: the row shows the user id, the username,
        the phone masked and whether the account is restricted.  A failure shows
        the exception class only, never its message, so nothing echoed by the
        server can put a credential or a phone into the report.
        """
        rows: list[tuple[str, bool, str]] = []
        credentials = None
        for account in self.resolve(names):
            path = Path(account.session)
            if not path.exists():
                rows.append((account.label, False, f"{SESSION_NOT_FOUND}: {path}"))
                continue
            if not live:
                rows.append((account.label, True, "dry-run: session file present"))
                continue
            if credentials is None:
                try:
                    credentials = _credentials(self.settings)
                except ConfigError as error:
                    rows.append((account.label, False, str(error)))
                    continue
            client = None
            try:
                client = _client(
                    client_factory, str(path), credentials, self.settings.proxy
                )
                client.connect()
                me = client.get_me()
                if me is None:
                    rows.append((account.label, False, "LOGIN_FAILED empty get_me"))
                    self.store.set_account_status(account.label, "error")
                    continue
                rows.append((account.label, True, _describe(me)))
                self.store.set_account_status(account.label, "ok")
            except Exception as error:  # noqa: BLE001 - reported as a row
                rows.append((account.label, False, type(error).__name__))
                self.store.set_account_status(account.label, "error")
            finally:
                # Every opened connection is closed here, including the early
                # ``continue`` above: a client left open keeps a loop task
                # pending, which is what printed "Task was destroyed" at exit.
                if client is not None:
                    with contextlib.suppress(Exception):
                        client.disconnect()
        return rows

    def dialogs(
        self, names: list[str] | None, live: bool, limit: int = 20, client_factory=None
    ) -> list[tuple[str, str, str, str]]:
        """The chats this account can see: ``(label, kind, ref, title)``.

        Read-only, and the only way to find a real group for the live checks:
        every other page is told which chats to touch, so without this the
        filter pages can only ever be exercised against a made-up reference.
        """
        rows: list[tuple[str, str, str, str]] = []
        credentials = None
        for account in self.resolve(names):
            path = Path(account.session)
            if not path.exists():
                rows.append((account.label, "?", "", f"{SESSION_NOT_FOUND}: {path}"))
                continue
            if not live:
                rows.append(
                    (account.label, "?", "", "dry-run: would list the newest dialogs")
                )
                continue
            if credentials is None:
                try:
                    credentials = _credentials(self.settings)
                except ConfigError as error:
                    rows.append((account.label, "?", "", str(error)))
                    continue
            client = None
            try:
                client = _client(
                    client_factory, str(path), credentials, self.settings.proxy
                )
                client.connect()
                for dialog in client.iter_dialogs(limit=limit):
                    entity = dialog.entity
                    if getattr(entity, "megagroup", False):
                        kind = "group"
                    elif getattr(entity, "broadcast", False):
                        kind = "channel"
                    elif getattr(entity, "bot", False):
                        kind = "bot"
                    elif dialog.is_user:
                        kind = "user"
                    else:
                        kind = "chat"
                    username = getattr(entity, "username", "") or ""
                    ref = f"@{username}" if username else str(dialog.id)
                    # Unread and the last message time are what make the listing
                    # useful: they say which chat is busy enough to watch.
                    unread = int(getattr(dialog, "unread_count", 0) or 0)
                    sent = getattr(dialog, "date", None)
                    stamp = sent.strftime("%H:%M") if sent else "-"
                    rows.append(
                        (
                            account.label,
                            kind,
                            ref,
                            f"{dialog.name or ''}\t{stamp}\t{unread}",
                        )
                    )
            except Exception as error:  # noqa: BLE001 - reported as a row
                rows.append((account.label, "?", "", type(error).__name__))
            finally:
                if client is not None:
                    with contextlib.suppress(Exception):
                        client.disconnect()
        return rows

    def spambot(
        self,
        names: list[str] | None,
        live: bool,
        client_factory=None,
        wait: float = 8.0,
        apply: bool = False,
    ) -> list[tuple[str, str, bool, str]]:
        """Ask @spambot about each account, the reference's own frozen check.

        ``rows`` are ``(label, kind, ok, detail)``. In dry-run nothing is sent:
        the row says what would be sent and to whom. Live sends ``/start`` and
        reads the newest message in that bot chat after ``wait`` seconds.
        """
        from .spambot import SPAMBOT, START, classify, headline, shorten

        rows: list[tuple[str, str, bool, str]] = []
        credentials = None
        for account in self.resolve(names):
            path = Path(account.session)
            if not path.exists():
                rows.append((account.label, "", False, f"{SESSION_NOT_FOUND}: {path}"))
                continue
            if not live:
                rows.append(
                    (
                        account.label,
                        "",
                        True,
                        f"dry-run: would send {START} to @{SPAMBOT}",
                    )
                )
                continue
            if credentials is None:
                try:
                    credentials = _credentials(self.settings)
                except ConfigError as error:
                    rows.append((account.label, "", False, str(error)))
                    continue
            client = None
            try:
                client = _client(
                    client_factory, str(path), credentials, self.settings.proxy
                )
                client.connect()
                client.send_message(SPAMBOT, START)
                reply = _latest_bot_reply(client, SPAMBOT, wait)
                kind, marker = classify(reply)
                rows.append(
                    (
                        account.label,
                        kind,
                        True,
                        f"{headline(kind)}"
                        + (f" [kw={marker}]" if marker else "")
                        + f" reply={shorten(reply) or '-'}",
                    )
                )
                if apply and kind:
                    self.store.set_account_kind(account.label, kind)
            except Exception as error:  # noqa: BLE001 - reported as a row
                rows.append((account.label, "", False, type(error).__name__))
            finally:
                if client is not None:
                    with contextlib.suppress(Exception):
                        client.disconnect()
        return rows

    def exit_frozen(
        self,
        names: list[str] | None,
        live: bool = False,
        wait: float = 8.0,
        client_factory=None,
        trash: str = "",
    ) -> list[tuple[str, bool, str]]:
        """The batch-login page's "Exit Frozen Accounts" routine.

        The page's own sentences (:1742495, :1742564-1742572): the start
        guard ("Unable to start exit frozen accounts: task running or no
        accounts selected"), the collection line ("Collecting frozen
        accounts"), the detection line per frozen account ("Detected frozen
        account #%1 (index %2)"), the empty-pool line ("No frozen accounts
        detected"), the start line ("Starting exit frozen accounts routine
        (%1 accounts)"), the abort/completion pair ("Exit frozen accounts
        aborted" / "Exit frozen accounts completed") and the per-account
        work line ("Processing frozen account #%1 (index %2, phone %3)"
        with "unknown phone" for an account that has no number).

        A frozen account is one the @spambot probe classifies as ``frozen``
        (:1742572's phone shows the page feeds each candidate through the
        same verdict). A live routine then logs every frozen account out -
        the same destructive step the console tool performs, so the session
        files move to the trash folder and are not unlinked.
        """
        from typing import Any

        from .spambot import SPAMBOT, headline

        rows: list[tuple[str, bool, str]] = []
        try:
            accounts = self.resolve(names)
        except AccountError:
            # An unknown label leaves the routine nothing to probe; the
            # page folds that into its own start guard (:1742564).
            accounts = []
        if not accounts:
            print(
                "Unable to start exit frozen accounts: task running or no "
                "accounts selected"
            )
            return rows
        print("Collecting frozen accounts")
        frozen: list[tuple[int, Any, str]] = []
        for index, account in enumerate(accounts):
            if not live:
                rows.append(
                    (
                        account.label,
                        True,
                        f"dry-run: would ask @{SPAMBOT} then log frozen ones out",
                    )
                )
                continue
            probe = self.spambot(
                [account.label], live=True, wait=wait,
                client_factory=client_factory,
            )
            label, kind, ok, detail = probe[0] if probe else (account.label, "", False, "")
            if not ok:
                rows.append((label, False, detail))
                continue
            if kind != "frozen":
                rows.append((label, True, f"not frozen ({headline(kind) or 'unclassified'})"))
                continue
            phone = account.phone or "unknown phone"
            print(f"Detected frozen account #{index + 1} (index {index})")
            frozen.append((index, account, phone))
        if not frozen:
            if live:
                print("No frozen accounts detected")
            return rows
        print(f"Starting exit frozen accounts routine ({len(frozen)} accounts)")
        for index, account, phone in frozen:
            print(
                f"Processing frozen account #{index + 1} "
                f"(index {index}, phone {phone})"
            )
            if not live:
                continue
            path = Path(account.session)
            if not path.exists():
                rows.append((account.label, False, f"{SESSION_NOT_FOUND}: {path}"))
                continue
            try:
                target_dir = (
                    Path(trash).expanduser()
                    if trash
                    else (Path(self.settings.data_dir) / "sessions" / "trash")
                )
                target_dir.mkdir(parents=True, exist_ok=True)
                moved = target_dir / path.name
                if moved.exists():
                    moved = (
                        target_dir / f"{path.stem}.{int(path.stat().st_mtime)}"
                        f"{path.suffix}"
                    )
                path.replace(moved)
                rows.append(
                    (
                        account.label,
                        True,
                        f"logged out, session moved to {moved}",
                    )
                )
            except OSError as error:
                rows.append(
                    (account.label, False, f"{type(error).__name__}: {error}")
                )
        if live:
            print("Exit frozen accounts completed")
        return rows

    def logout(
        self, names: list[str] | None, live: bool = False, trash: str = ""
    ) -> list[tuple[str, bool, str]]:
        """The console tool: "Batch logout" (1858834-1858838).

        The reference's four lines are "Batch logout already running", "Batch
        logout completed", "Batch logout is not available" and "Batch logout
        already running". A logout is destructive - the session file is the
        only copy of the login - so this MOVES it to a trash directory instead
        of unlinking it, and says where it went. That is a deliberate
        difference, reported rather than hidden: a rebuild that can undo a
        mistaken batch logout is strictly safer than one that cannot.
        """
        rows: list[tuple[str, bool, str]] = []
        target_dir = (
            Path(trash).expanduser()
            if trash
            else (Path(self.settings.data_dir) / "sessions" / "trash")
        )
        for account in self.resolve(names):
            path = Path(account.session)
            if not path.exists():
                rows.append((account.label, False, f"{SESSION_NOT_FOUND}: {path}"))
                continue
            if not live:
                rows.append(
                    (account.label, True, f"dry-run: would log out (move {path.name})")
                )
                continue
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                moved = target_dir / path.name
                if moved.exists():
                    moved = (
                        target_dir / f"{path.stem}.{int(path.stat().st_mtime)}"
                        f"{path.suffix}"
                    )
                path.replace(moved)
            except OSError as error:
                rows.append((account.label, False, f"{type(error).__name__}: {error}"))
                continue
            rows.append(
                (
                    account.label,
                    True,
                    f"Batch logout completed, session moved to {moved}",
                )
            )
        return rows

    def passwords(
        self,
        names: list[str] | None,
        new_password: str,
        live: bool,
        client_factory=None,
        current_password: str = "",
    ) -> list[tuple[str, bool, str]]:
        """Batch modify cloud passwords for the selected accounts.

        The console tool is the reference's own ("Batch modify cloud passwords
        for selected accounts", :1737291) and so are its outcomes: "Account %1
        modify successfully via %2" names the source that answered
        (:1737299-1737301), the sync verdicts follow (:1737302-1737304), the
        failures speak the page's own sentences (:1737296, :1737300), and the
        closing line is "Password change task finished. Success: %1/%2"
        (:1737305).

        The old password is the page's three-source model: the box's default
        first ("input old password", the freshest statement of intent), the
        account's recorded metadata second ("metadata old password"), and when
        both are absent the account is assumed to have none ("empty old
        password"). After a live change the new password goes back into the
        same metadata (:1737302), so the next run reads it as its own old
        password, and a sidecar ``.json`` next to the session is synced too
        (:1737303).

        The passwords never reach the log or the terminal: rows carry the
        account, a verdict and the reference's own sentences, nothing else.
        """
        rows: list[tuple[str, bool, str]] = []
        if not new_password:
            labels = [account.label for account in self.resolve(names)] or ["-"]
            return [(label, False, "no new password given") for label in labels]
        credentials = None
        try:
            for account in self.resolve(names):
                path = Path(account.session)
                if not path.exists():
                    rows.append((account.label, False, f"{SESSION_NOT_FOUND}: {path}"))
                    continue
                recorded = self.store.account_password(account.label)
                if current_password:
                    old_password = current_password
                    source = "input old password"
                elif recorded:
                    old_password = recorded
                    source = "metadata old password"
                else:
                    old_password = ""
                    source = "empty old password"
                if not live:
                    detail = f"dry-run: would change the cloud password (via {source})"
                    rows.append((account.label, True, detail))
                    continue
                if credentials is None:
                    try:
                        credentials = _credentials(self.settings)
                    except ConfigError as error:
                        rows.append((account.label, False, str(error)))
                        continue
                client = None
                try:
                    client = _client(
                        client_factory, str(path), credentials, self.settings.proxy
                    )
                    client.connect()
                    client.edit_2fa(
                        current_password=old_password or None, new_password=new_password
                    )
                    rows.append(
                        (
                            account.label,
                            True,
                            f"Account {account.label} modify successfully via {source}",
                        )
                    )
                    print(
                        f"Account {account.label} password updated "
                        f"{self._sync_password(account, new_password, path)}"
                    )
                except Exception as error:  # noqa: BLE001 - reported as a row
                    rows.append(
                        (
                            account.label,
                            False,
                            f"Account {account.label} failed to change "
                            f"password. Account {account.label} "
                            f"({account.phone}) error: "
                            f"{type(error).__name__}: {error}",
                        )
                    )
                finally:
                    if client is not None:
                        with contextlib.suppress(Exception):
                            client.disconnect()
        except KeyboardInterrupt:
            # The page's own Stop: the run gives back what it finished and
            # says so, and the closing line counts against the whole plan.
            print("Stop requested...")
        return rows

    def _sync_password(self, account: Account, new_password: str, session: Path) -> str:
        """The reference's sync verdict for one successful change.

        The store record is the metadata side ("and metadata synced.",
        :1737302); a sidecar ``.json`` next to the session is the JSON side
        ("and JSON synced.", :1737303). When the store write fails outright
        nothing is synced and the verdict says so (:1737304). The password
        value never appears in any verdict.
        """
        try:
            self.store.set_account_password(account.label, new_password)
        except Exception:  # noqa: BLE001 - the verdict is the report
            return "(metadata sync skipped)."
        sidecar = session.with_suffix(".json")
        if sidecar.exists():
            try:
                raw = json.loads(sidecar.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = None
            if isinstance(raw, dict):
                raw["password"] = new_password
                try:
                    sidecar.write_text(
                        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                except OSError:
                    pass
                else:
                    return "and JSON synced."
        return "and metadata synced."

    def passkeys(
        self, names: list[str] | None, out_dir: str | Path, live: bool
    ) -> list[tuple[str, bool, str]]:
        """Export metadata-only passkey files for the selected accounts.

        The password page's second task (:1737294): one ``.passkey`` JSON per
        account in the export folder, carrying what the reference's own file
        carries - the phone number, which the login side later reads back
        ("Passkey file missing PhoneNumber: %1", :1737361). The build's own
        boundary is kept verbatim: metadata only, because the private key is
        not available in this build (:1737384).
        """
        rows: list[tuple[str, bool, str]] = []
        folder = Path(out_dir).expanduser()
        if live:
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                return [
                    (
                        account.label,
                        False,
                        f"Failed to create passkey directory: {error}",
                    )
                    for account in self.resolve(names)
                ]
        for account in self.resolve(names):
            if not account.phone:
                rows.append(
                    (
                        account.label,
                        False,
                        f"Account {account.label} has no phone number, skipped.",
                    )
                )
                continue
            if not live:
                rows.append(
                    (
                        account.label,
                        True,
                        "dry-run: would export a metadata passkey file",
                    )
                )
                continue
            try:
                path = _write_passkey(folder, account.label, account.phone)
                rows.append(
                    (
                        account.label,
                        True,
                        f"Exported metadata passkey file for "
                        f"account {account.label} to {path}",
                    )
                )
            except OSError as error:
                rows.append((account.label, False, f"{type(error).__name__}: {error}"))
        return rows


def _write_passkey(folder: Path, label: str, phone: str) -> Path:
    """One ``.passkey`` file, the name pattern the reference generates.

    ``passkey%1_%2`` (:1737371) with the account and a timestamp; the JSON
    holds the metadata the login side validates, and the phone number is
    mandatory there, which is why an account without one is skipped whole.
    """
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    path = folder / f"passkey{label}_{stamp}.passkey"
    payload = {
        "version": 1,
        "kind": "metadata",
        # The field name the login side validates ("Passkey file missing
        # PhoneNumber: %1", :1737361) - the same name the reference's own
        # file carries, so an export feeds the login task unchanged.
        "PhoneNumber": phone,
        "label": label,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")
    return path


def passkey_login(
    files: Sequence[Path | str], live: bool
) -> list[tuple[str, bool, str]]:
    """The batch_login page's passkey task body (:1737357-1737378).

    One row per file, labelled by the stem: the reference's own block line
    names ``%1.passkey`` (:1737377). The reader validates what the
    reference's own reader validates - open, JSON, the ``PhoneNumber`` field
    (:1737357-1737361) - and a valid record still blocks, because secure
    replay is not implemented in this build (:1737375, :1737377). The page's
    Start gate (the classifier and its mixed refusal, :1742499) runs before
    this function and is the CLI's, not here.
    """
    rows: list[tuple[str, bool, str]] = []
    for path in (Path(item) for item in files):
        stem = path.stem
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            rows.append((stem, False, f"Failed to open passkey file: {path}"))
            continue
        except ValueError:
            rows.append((stem, False, f"Invalid passkey file format: {path}"))
            continue
        if not isinstance(payload, dict) or not payload.get("PhoneNumber"):
            rows.append((stem, False, f"Passkey file missing PhoneNumber: {path}"))
            continue
        if not live:
            rows.append(
                (
                    stem,
                    True,
                    "dry-run: would attempt passkey login; "
                    "replay is blocked in this build",
                )
            )
            continue
        rows.append(
            (
                stem,
                False,
                f"Passkey login blocked for {stem}.passkey: "
                "secure replay is not implemented in this build.",
            )
        )
    return rows


def _latest_bot_reply(client, username: str, wait: float) -> str:
    """The newest text in that bot chat, polled for ``wait`` seconds.

    A bot answers when it answers, so this is a bounded wait rather than one
    read: it stops as soon as a text message exists.
    """
    import time

    deadline = time.monotonic() + max(0.0, wait)
    while True:
        try:
            messages = client.get_messages(username, limit=5)
        except Exception:  # noqa: BLE001 - an empty answer is a valid answer
            messages = []
        for message in messages or []:
            text = getattr(message, "message", "") or ""
            if text:
                return text
        if time.monotonic() >= deadline:
            return ""
        time.sleep(1.0)


def _credentials(settings: Settings):
    from .login import resolve_credentials

    return resolve_credentials(settings)


def _client(factory, path: str, credentials, proxy=None):
    """Build the client with the configured proxy, as a tuple, not a string."""
    if factory is not None:
        return factory(path, credentials)
    from .login import build_client

    return build_client(path, credentials, proxy)


def _describe(me) -> str:
    from .login import mask_phone

    username = getattr(me, "username", "") or ""
    restricted = "yes" if getattr(me, "restricted", False) else "no"
    return (
        f"user_id={getattr(me, 'id', '')} "
        f"username={('@' + username) if username else '-'} "
        f"phone={mask_phone(getattr(me, 'phone', '') or '')} "
        f"restricted={restricted}"
    )


def _proxy_row(settings: Settings) -> str:
    """The ``accounts.proxy`` column (TEXT), not an argument for Telethon.

    A client needs ``ProxyConfig.to_telethon()``; this string is only what the
    pool table shows and what SQLite can store.
    """
    proxy = settings.proxy
    if not proxy:
        return ""
    return f"{proxy.kind}://{proxy.host}:{proxy.port}"
