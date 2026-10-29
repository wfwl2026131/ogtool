"""Configuration: one global ``config.yaml`` plus one YAML file per task.

The global file holds what every feature needs (api credentials, where the
sessions, the database and the logs live, proxy defaults, the AI endpoint).
A task file holds what one run needs and is merged over the global one.

Values may reference the environment as ``${NAME}`` or ``${NAME:default}``, so
credentials never have to sit in a file that ends up in git.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """A configuration file is missing, unreadable or inconsistent."""


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")


def _expand(value: Any) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise ConfigError(f"environment variable {name} is not set")

        return _ENV_REF.sub(replace, value)
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item) for item in value]
    return value


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Read one YAML file and expand its environment references."""
    target = Path(path).expanduser()
    if not target.exists():
        raise ConfigError(f"config file not found: {target}")
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        raise ConfigError(f"{target}: invalid YAML: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"{target}: the top level must be a mapping")
    return _expand(raw)


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge ``override`` into ``base`` without mutating either."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass(slots=True)
class AIConfig:
    """An OpenAI compatible chat completion endpoint."""

    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: float = 60.0

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


@dataclass(slots=True)
class CheckmeConfig:
    """The post-join verification service (the reference's checkme keys).

    The four knobs are the reference client's own global settings
    (``checkmeBaseUrl`` / ``checkmeEnabled`` / ``checkmeMaxRound`` /
    ``checkmeDelaySec``, og-strings.txt :1743074-1743077): a small HTTP
    service the client polls at ``<base>/api/__checkme__`` (:1739935) after
    each join, following the decision it answers with. ``enabled`` and an
    empty ``base_url`` mean the whole chain is off.
    """

    base_url: str = ""
    enabled: bool = False
    max_round: int = 5
    delay_sec: float = 5.0

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.base_url.strip())


@dataclass(slots=True)
class ProxyConfig:
    """One proxy, in the shape Telethon expects."""

    kind: str = ""
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""

    def to_telethon(self) -> Any:
        """The proxy tuple Telethon expects, in its string form.

        ``("socks5", host, port, True, user, pass)`` avoids importing PySocks
        just for the numeric constants: Telethon 1.45 accepts the protocol name
        and hands the connection to ``python-socks``, which is the package that
        actually opens the socket.
        """
        kind = self.kind.lower()
        if kind in ("socks5", "socks4"):
            return (
                kind,
                self.host,
                self.port,
                True,
                self.username or None,
                self.password or None,
            )
        raise ConfigError(f"unsupported proxy kind: {self.kind}")


@dataclass(slots=True)
class GlobalConfig:
    """The non licence knobs the reference client keeps in its settings page.

    Only configuration and validation live here: ``restart_interval`` and
    ``restart_timed`` describe when a restart *would* be due, and nothing in
    this package ever restarts itself.
    """

    account_batch: int = 20
    threads_max: int = 128
    resume_delay: float = 0.0
    tp_overload: int = 0
    restart_interval: float = 0.0
    restart_timed: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> GlobalConfig:
        data = raw.get("globals") or {}
        if not isinstance(data, dict):
            raise ConfigError("globals: the section must be a mapping")
        return cls(
            account_batch=_positive_int(data, "account_batch", 20),
            threads_max=_positive_int(data, "threads_max", 128),
            resume_delay=_number(data, "resume_delay", 0.0),
            tp_overload=_non_negative_int(data, "tp_overload", 0),
            restart_interval=_number(data, "restart_interval", 0.0),
            restart_timed=bool(data.get("restart_timed", False)),
        )

    def clamp_threads(self, value: int) -> int:
        """A task may ask for fewer threads, never for more than the pool."""
        return max(1, min(int(value), self.threads_max))

    def restart_due(self, uptime: float) -> bool:
        """Whether an uptime would trigger a restart, given the interval."""
        if self.restart_interval <= 0:
            return False
        return uptime >= self.restart_interval

    def overload_check(self, overload: int) -> bool:
        """``tp_overload == 0`` means the check is off, as on the page."""
        if self.tp_overload <= 0:
            return True
        return overload < self.tp_overload


def _number(data: dict[str, Any], key: str, default: float) -> float:
    value = data.get(key)
    if value is None or value == "":
        return default
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ConfigError(f"globals.{key}: not a number: {value!r}") from error
    if result < 0:
        raise ConfigError(f"globals.{key}: must not be negative: {result}")
    return result


def _positive_int(data: dict[str, Any], key: str, default: int) -> int:
    value = _number(data, key, default)
    if int(value) != value or value < 1:
        raise ConfigError(f"globals.{key}: must be a whole number >= 1")
    return int(value)


def _non_negative_int(data: dict[str, Any], key: str, default: int) -> int:
    value = _number(data, key, default)
    if int(value) != value:
        raise ConfigError(f"globals.{key}: must be a whole number")
    return int(value)


@dataclass(slots=True)
class Settings:
    """Everything the tool needs before a feature runs."""

    path: Path
    api_id: int = 0
    api_hash: str = ""
    data_dir: Path = field(default_factory=lambda: Path("data"))
    session_dir: Path = field(default_factory=lambda: Path("sessions"))
    db_path: Path = field(default_factory=lambda: Path("og.sqlite"))
    log_dir: Path = field(default_factory=lambda: Path("logs"))
    proxy: ProxyConfig | None = None
    ai: AIConfig = field(default_factory=AIConfig)
    checkme: CheckmeConfig = field(default_factory=CheckmeConfig)
    runtime: GlobalConfig = field(default_factory=GlobalConfig)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> Settings:
        target = Path(path).expanduser()
        raw = load_yaml(target)
        base = target.parent

        def resolve(key: str, default: str) -> Path:
            value = raw.get(key) or default
            candidate = Path(str(value)).expanduser()
            return candidate if candidate.is_absolute() else (base / candidate)

        ai_raw = raw.get("ai") or {}
        proxy_raw = raw.get("proxy") or {}
        proxy = None
        if proxy_raw.get("host"):
            proxy = ProxyConfig(
                kind=str(proxy_raw.get("kind", "socks5")),
                host=str(proxy_raw["host"]),
                port=int(proxy_raw.get("port", 0)),
                username=str(proxy_raw.get("username", "")),
                password=str(proxy_raw.get("password", "")),
            )
        settings = cls(
            path=target,
            api_id=int(raw.get("api_id") or 0),
            api_hash=str(raw.get("api_hash") or ""),
            data_dir=resolve("data_dir", "."),
            session_dir=resolve("session_dir", "sessions"),
            db_path=resolve("db", "og.sqlite"),
            log_dir=resolve("logs", "logs"),
            proxy=proxy,
            ai=AIConfig(
                base_url=str(ai_raw.get("base_url", "")),
                api_key=str(ai_raw.get("api_key", "")),
                model=str(ai_raw.get("model", "")),
                timeout=float(ai_raw.get("timeout", 60.0)),
            ),
            runtime=GlobalConfig.from_dict(raw),
            checkme=CheckmeConfig(
                base_url=str((raw.get("checkme") or {}).get("base_url", "")),
                enabled=bool((raw.get("checkme") or {}).get("enabled", False)),
                max_round=int((raw.get("checkme") or {}).get("max_round", 5)),
                delay_sec=float((raw.get("checkme") or {}).get("delay_sec", 5.0)),
            ),
            raw=raw,
        )
        settings.ensure_dirs()
        return settings

    def ensure_dirs(self) -> None:
        for directory in (self.session_dir, self.log_dir, self.db_path.parent):
            directory.mkdir(parents=True, exist_ok=True)

    def require_credentials(self) -> tuple[int, str]:
        """api_id and api_hash, for the paths that really talk to Telegram.

        The config file may leave both empty: the pair is then read from the
        environment through :func:`ogtool.login.resolve_credentials`, which
        understands the ``$DSH_SECRET_TG_API_CREDENTIALS`` blob.  That keeps the
        plain values out of a file that people copy around.
        """
        if self.api_id and self.api_hash:
            return int(self.api_id), str(self.api_hash)
        from .login import resolve_credentials

        found = resolve_credentials(self)
        return found.api_id, found.api_hash

    def session_path(self, label: str) -> Path:
        return self.session_dir / f"{label}.session"


def load_task(path: str | Path, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read a task file and merge it over the global settings."""
    task = load_yaml(path)
    return merge(base or {}, task)
