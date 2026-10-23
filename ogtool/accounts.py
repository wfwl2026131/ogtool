"""The account pool: sessions on disk plus the rows that describe them."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Settings
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
            proxy=_proxy_text(self.settings),
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
            proxy=_proxy_text(self.settings),
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
            account = by_label.get(name) or by_label.get(label_for(name)) \
                if name.replace("+", "").isdigit() else by_label.get(name)
            if account is None:
                raise AccountError(f"{SESSION_NOT_FOUND}: {name}")
            chosen.append(account)
        return chosen

    def test(self, names: list[str] | None, live: bool) -> list[tuple[str, bool, str]]:
        """Connectivity check. In dry-run it only proves the file is there."""
        rows: list[tuple[str, bool, str]] = []
        for account in self.resolve(names):
            path = Path(account.session)
            if not path.exists():
                rows.append((account.label, False, f"{SESSION_NOT_FOUND}: {path}"))
                continue
            if not live:
                rows.append((account.label, True, "dry-run: session file present"))
                continue
            try:
                from telethon.sync import TelegramClient
            except ImportError:  # pragma: no cover - live only
                rows.append((account.label, False, "telethon is not installed"))
                continue
            api_id, api_hash = self.settings.require_credentials()
            try:
                client = TelegramClient(str(path), api_id, api_hash)
                client.connect()
                me = client.get_me()
                rows.append((account.label, True,
                             f"@{getattr(me, 'username', '') or me.id}"))
                client.disconnect()
                self.store.set_account_status(account.label, "ok")
            except Exception as error:  # noqa: BLE001 - reported as a row
                rows.append((account.label, False, f"{type(error).__name__}: {error}"))
                self.store.set_account_status(account.label, "error")
        return rows


def _proxy_text(settings: Settings) -> str:
    proxy = settings.proxy
    if not proxy:
        return ""
    return f"{proxy.kind}://{proxy.host}:{proxy.port}"
