"""SQLite storage, shaped after the tables the reference client keeps.

Tables: ``accounts``, ``tasks``, ``target_lists``, ``targets``, ``results``,
``logs``, ``proxies`` and ``ai_calls``.  Everything a feature produces goes through here so a later port back
into the client can copy whole tables instead of re-deriving state.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL UNIQUE,
    phone       TEXT NOT NULL DEFAULT '',
    session     TEXT NOT NULL DEFAULT '',
    proxy       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'new',
    added_at    TEXT NOT NULL,
    last_seen   TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    feature     TEXT NOT NULL,
    config      TEXT NOT NULL DEFAULT '{}',
    mode        TEXT NOT NULL DEFAULT 'dry-run',
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    planned     INTEGER NOT NULL DEFAULT 0,
    sent        INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS target_lists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL DEFAULT 'custom',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS targets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id     INTEGER NOT NULL REFERENCES target_lists(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    ref         TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    UNIQUE (list_id, kind, ref)
);

CREATE TABLE IF NOT EXISTS results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    account     TEXT NOT NULL DEFAULT '',
    target      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'planned',
    error_code  TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    level       TEXT NOT NULL DEFAULT 'info',
    message     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proxies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL DEFAULT 'socks5',
    host        TEXT NOT NULL,
    port        INTEGER NOT NULL DEFAULT 0,
    user        TEXT NOT NULL DEFAULT '',
    password    TEXT NOT NULL DEFAULT '',
    latency_ms  INTEGER NOT NULL DEFAULT -1,
    status      TEXT NOT NULL DEFAULT 'new',
    note        TEXT NOT NULL DEFAULT '',
    added_at    TEXT NOT NULL,
    UNIQUE (kind, host, port, user)
);

CREATE TABLE IF NOT EXISTS ai_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    account     TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT '',
    prompt      TEXT NOT NULL DEFAULT '',
    response    TEXT NOT NULL DEFAULT '',
    tokens      INTEGER NOT NULL DEFAULT 0,
    elapsed_ms  INTEGER NOT NULL DEFAULT 0,
    ok          INTEGER NOT NULL DEFAULT 1,
    error       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    label       TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT '',
    username    TEXT NOT NULL DEFAULT '',
    phone       TEXT NOT NULL DEFAULT '',
    device      TEXT NOT NULL DEFAULT '',
    twofa       TEXT NOT NULL DEFAULT '',
    last_active TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_results_task ON results(task_id);
CREATE INDEX IF NOT EXISTS idx_ai_task ON ai_calls(task_id);
CREATE INDEX IF NOT EXISTS idx_results_created ON results(created_at);
CREATE INDEX IF NOT EXISTS idx_logs_task ON logs(task_id);
"""


def utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def today() -> str:
    return date.today().isoformat()


def local_day_bounds(now: datetime | None = None) -> tuple[str, str]:
    """The half open UTC interval that is "today" on this machine.

    Rows carry a UTC stamp, while the page's ``daily limit`` is a calendar day
    for whoever is reading it.  Comparing the first ten characters of a UTC
    stamp with a local date counts zero for the first hours of a local day east
    of UTC (00:00-08:00 in UTC+8), so the daily limit would silently stop
    applying.  Converting the two boundaries is the comparison that holds on
    both sides of midnight.
    """
    local = (now or datetime.now()).astimezone()
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return (start.astimezone(UTC).replace(microsecond=0).isoformat(),
            end.astimezone(UTC).replace(microsecond=0).isoformat())


@dataclass(slots=True)
class Account:
    label: str
    phone: str = ""
    session: str = ""
    proxy: str = ""
    status: str = "new"
    notes: str = ""


@dataclass(slots=True)
class Profile:
    """What #22 exports about one account, next to its session file."""

    label: str
    user_id: str = ""
    username: str = ""
    phone: str = ""
    device: str = ""
    twofa: str = ""
    last_active: str = ""

    def masked_phone(self) -> str:
        """``+15551230001`` -> ``+1555***0001``; short numbers stay as they are."""
        digits = self.phone or ""
        if len(digits) < 8:
            return digits or "-"
        head, tail = digits[:5], digits[-4:]
        return f"{head}***{tail}"


@dataclass(slots=True)
class Proxy:
    """One proxy row: what the proxy page's table shows and edits."""

    kind: str
    host: str
    port: int
    user: str = ""
    password: str = ""
    latency_ms: int = -1
    status: str = "new"
    note: str = ""
    id: int = 0

    def url(self) -> str:
        auth = f"{self.user}:{self.password}@" if self.user else ""
        return f"{self.kind}://{auth}{self.host}:{self.port}"

    def display(self) -> str:
        """The URL with the password masked, for anything that gets printed."""
        if not self.user:
            return f"{self.kind}://{self.host}:{self.port}"
        secret = "***" if self.password else ""
        auth = f"{self.user}:{secret}@" if secret else f"{self.user}@"
        return f"{self.kind}://{auth}{self.host}:{self.port}"


@dataclass(slots=True)
class TaskRef:
    id: int
    name: str
    feature: str


class Store:
    """A thin, explicit wrapper over one SQLite file."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A threaded run writes results from several lanes at once, so the
        # connection has to accept a call from any thread and be serialized
        # here: sqlite objects are not shareable on their own.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.RLock()
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # accounts ---------------------------------------------------------

    def add_account(self, account: Account) -> Account:
        self._conn.execute(
            """INSERT INTO accounts (label, phone, session, proxy, status,
                                     added_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(label) DO UPDATE SET
                   phone=excluded.phone, session=excluded.session,
                   proxy=excluded.proxy, status=excluded.status,
                   notes=excluded.notes""",
            (account.label, account.phone, account.session, account.proxy,
             account.status, utcnow(), account.notes),
        )
        self._conn.commit()
        return account

    def get_account(self, label: str) -> Account | None:
        row = self._conn.execute(
            "SELECT * FROM accounts WHERE label = ?", (label,)
        ).fetchone()
        return self._account(row) if row else None

    def list_accounts(self, limit: int = 0, offset: int = 0) -> list[Account]:
        """The pool in id order; ``limit``/``offset`` page it when asked."""
        sql = "SELECT * FROM accounts ORDER BY id"
        params: tuple[int, ...] = ()
        if limit:
            sql += " LIMIT ? OFFSET ?"
            params = (int(limit), int(offset))
        rows = self._conn.execute(sql, params).fetchall()
        return [self._account(row) for row in rows]

    def count_accounts(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()
        return int(row["n"])

    def set_account_status(self, label: str, status: str, last_seen: str = "") -> None:
        self._conn.execute(
            "UPDATE accounts SET status = ?, last_seen = ? WHERE label = ?",
            (status, last_seen or utcnow(), label),
        )
        self._conn.commit()

    @staticmethod
    def _account(row: sqlite3.Row) -> Account:
        return Account(
            label=row["label"], phone=row["phone"], session=row["session"],
            proxy=row["proxy"], status=row["status"], notes=row["notes"],
        )

    # proxies ----------------------------------------------------------

    def add_proxy(self, proxy: Proxy) -> Proxy:
        self._conn.execute(
            """INSERT INTO proxies (kind, host, port, user, password,
                                    latency_ms, status, note, added_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(kind, host, port, user) DO UPDATE SET
                   password=excluded.password, note=excluded.note,
                   latency_ms=excluded.latency_ms, status=excluded.status""",
            (proxy.kind, proxy.host, proxy.port, proxy.user, proxy.password,
             proxy.latency_ms, proxy.status, proxy.note, utcnow()),
        )
        self._conn.commit()
        row = self._conn.execute(
            """SELECT * FROM proxies WHERE kind = ? AND host = ?
               AND port = ? AND user = ?""",
            (proxy.kind, proxy.host, proxy.port, proxy.user),
        ).fetchone()
        return self._proxy(row) if row else proxy

    def list_proxies(self) -> list[Proxy]:
        rows = self._conn.execute("SELECT * FROM proxies ORDER BY id").fetchall()
        return [self._proxy(row) for row in rows]

    def delete_proxy(self, proxy_id: int) -> bool:
        cursor = self._conn.execute("DELETE FROM proxies WHERE id = ?",
                                    (proxy_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def clear_proxies(self) -> int:
        cursor = self._conn.execute("DELETE FROM proxies")
        self._conn.commit()
        return cursor.rowcount

    def set_proxy_latency(self, proxy_id: int, latency_ms: int,
                          status: str = "ok") -> None:
        self._conn.execute(
            "UPDATE proxies SET latency_ms = ?, status = ? WHERE id = ?",
            (latency_ms, status, proxy_id),
        )
        self._conn.commit()

    def bind_proxy(self, label: str, proxy: Proxy | None) -> None:
        text = proxy.url() if proxy else ""
        self._conn.execute("UPDATE accounts SET proxy = ? WHERE label = ?",
                           (text, label))
        self._conn.commit()

    @staticmethod
    def _proxy(row: sqlite3.Row) -> Proxy:
        return Proxy(
            kind=row["kind"], host=row["host"], port=int(row["port"]),
            user=row["user"], password=row["password"],
            latency_ms=int(row["latency_ms"]), status=row["status"],
            note=row["note"], id=int(row["id"]),
        )

    # tasks ------------------------------------------------------------

    def create_task(
        self,
        name: str,
        feature: str,
        config: dict[str, Any] | None = None,
        mode: str = "dry-run",
        planned: int = 0,
    ) -> TaskRef:
        cursor = self._conn.execute(
            """INSERT INTO tasks (name, feature, config, mode, status,
                                  created_at, planned)
               VALUES (?, ?, ?, ?, 'running', ?, ?)""",
            (name, feature, json.dumps(config or {}, ensure_ascii=False),
             mode, utcnow(), planned),
        )
        self._conn.commit()
        return TaskRef(id=int(cursor.lastrowid), name=name, feature=feature)

    def task_row(self, task_id: int) -> sqlite3.Row | None:
        """One task as stored, so a caller can read the counters it closed."""
        return self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()

    def finish_task(self, task_id: int, sent: int = 0, failed: int = 0,
                    status: str = "done") -> None:
        self._conn.execute(
            """UPDATE tasks SET status = ?, finished_at = ?, sent = ?, failed = ?
               WHERE id = ?""",
            (status, utcnow(), sent, failed, task_id),
        )
        self._conn.commit()

    # targets ----------------------------------------------------------

    def save_target_list(self, name: str, kind: str,
                         items: Iterable[tuple[str, str, str]]) -> int:
        cursor = self._conn.execute(
            """INSERT INTO target_lists (name, kind, created_at) VALUES (?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET kind = excluded.kind""",
            (name, kind, utcnow()),
        )
        row = self._conn.execute(
            "SELECT id FROM target_lists WHERE name = ?", (name,)
        ).fetchone()
        list_id = int(row["id"])
        del cursor
        self._conn.execute("DELETE FROM targets WHERE list_id = ?", (list_id,))
        for item_kind, ref, title in items:
            self._conn.execute(
                """INSERT OR IGNORE INTO targets (list_id, kind, ref, title)
                   VALUES (?, ?, ?, ?)""",
                (list_id, item_kind, ref, title),
            )
        self._conn.commit()
        return int(list_id)

    # results / logs ---------------------------------------------------

    def add_result(self, task_id: int | None, account: str, target: str,
                   status: str, error_code: str = "", detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO results (task_id, account, target, status,
                                        error_code, detail, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (task_id, account, target, status, error_code, detail,
                 utcnow()),
            )
            self._conn.commit()

    # ai calls ---------------------------------------------------------

    def add_ai_call(self, task_id: int | None, *, account: str = "",
                    kind: str = "", prompt: str = "", response: str = "",
                    tokens: int = 0, elapsed_ms: int = 0, ok: bool = True,
                    error: str = "") -> None:
        """One audit row per model call, whatever the outcome."""
        self._conn.execute(
            """INSERT INTO ai_calls (task_id, account, kind, prompt, response,
                                     tokens, elapsed_ms, ok, error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, account, kind, prompt, response, int(tokens),
             int(elapsed_ms), 1 if ok else 0, error, utcnow()),
        )
        self._conn.commit()

    def ai_calls_for(self, task_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM ai_calls WHERE task_id = ? ORDER BY id", (task_id,)
        ).fetchall()

    def count_sent_today(self, account: str,
                         now: datetime | None = None) -> int:
        start, end = local_day_bounds(now)
        row = self._conn.execute(
            """SELECT COUNT(*) AS n FROM results
               WHERE account = ? AND status = 'sent'
                 AND created_at >= ? AND created_at < ?""",
            (account, start, end),
        ).fetchone()
        return int(row["n"])

    def add_log(self, task_id: int | None, message: str, level: str = "info") -> None:
        self._conn.execute(
            "INSERT INTO logs (task_id, level, message, created_at) VALUES (?, ?, ?, ?)",
            (task_id, level, message, utcnow()),
        )
        self._conn.commit()

    def results_for(self, task_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM results WHERE task_id = ? ORDER BY id", (task_id,)
        ).fetchall()

    def results_for_feature(self, feature: str) -> list[sqlite3.Row]:
        """Every result row of one page, across the tasks it opened.

        A page that runs another page inside it (mix_market does) writes rows
        under more than one task id, so the task at hand cannot show what the
        whole run did.
        """
        return self._conn.execute(
            """SELECT r.* FROM results r
               JOIN tasks t ON t.id = r.task_id
               WHERE t.feature = ? ORDER BY r.id""",
            (feature,),
        ).fetchall()

    def sent_pairs(self, feature: str) -> set[tuple[str, str]]:
        """``(account, target)`` pairs this feature already delivered.

        That is what makes a rerun resumable: the pairs come out of the
        results table, so a task can be stopped and started again without
        sending anything twice.
        """
        rows = self._conn.execute(
            """SELECT r.account, r.target FROM results r
               JOIN tasks t ON t.id = r.task_id
               WHERE t.feature = ? AND r.status = 'sent'""",
            (feature,),
        ).fetchall()
        return {(row["account"], row["target"]) for row in rows}

    # profiles ---------------------------------------------------------

    def save_profile(self, profile: Profile) -> Profile:
        self._conn.execute(
            """INSERT INTO profiles (label, user_id, username, phone, device,
                                     twofa, last_active, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(label) DO UPDATE SET
                   user_id=excluded.user_id, username=excluded.username,
                   phone=excluded.phone, device=excluded.device,
                   twofa=excluded.twofa, last_active=excluded.last_active,
                   updated_at=excluded.updated_at""",
            (profile.label, profile.user_id, profile.username, profile.phone,
             profile.device, profile.twofa, profile.last_active, utcnow()),
        )
        self._conn.commit()
        return profile

    def get_profile(self, label: str) -> Profile | None:
        row = self._conn.execute(
            "SELECT * FROM profiles WHERE label = ?", (label,)
        ).fetchone()
        return self._profile(row) if row else None

    def list_profiles(self) -> list[Profile]:
        rows = self._conn.execute(
            "SELECT * FROM profiles ORDER BY label"
        ).fetchall()
        return [self._profile(row) for row in rows]

    @staticmethod
    def _profile(row: sqlite3.Row) -> Profile:
        return Profile(
            label=row["label"], user_id=row["user_id"],
            username=row["username"], phone=row["phone"],
            device=row["device"], twofa=row["twofa"],
            last_active=row["last_active"],
        )

    def stats(self) -> dict[str, int]:
        def one(sql: str) -> int:
            row = self._conn.execute(sql).fetchone()
            return int(row[0])

        return {
            "accounts": one("SELECT COUNT(*) FROM accounts"),
            "tasks": one("SELECT COUNT(*) FROM tasks"),
            "results": one("SELECT COUNT(*) FROM results"),
            "logs": one("SELECT COUNT(*) FROM logs"),
            "ai_calls": one("SELECT COUNT(*) FROM ai_calls"),
            "profiles": one("SELECT COUNT(*) FROM profiles"),
        }
