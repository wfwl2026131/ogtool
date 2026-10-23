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
class ProxyConfig:
    """One proxy, in the shape Telethon expects."""

    kind: str = ""
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""

    def to_telethon(self) -> Any:
        import socks
        from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate  # noqa: F401

        kind = self.kind.lower()
        if kind in ("socks5", "socks4"):
            protocol = socks.SOCKS5 if kind == "socks5" else socks.SOCKS4
            return (protocol, self.host, self.port, True,
                    self.username or None, self.password or None)
        raise ConfigError(f"unsupported proxy kind: {self.kind}")


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
            raw=raw,
        )
        settings.ensure_dirs()
        return settings

    def ensure_dirs(self) -> None:
        for directory in (self.session_dir, self.log_dir, self.db_path.parent):
            directory.mkdir(parents=True, exist_ok=True)

    def require_credentials(self) -> tuple[int, str]:
        """api_id and api_hash, for the paths that really talk to Telegram."""
        if not self.api_id or not self.api_hash:
            raise ConfigError(
                "api_id and api_hash are required for --live; "
                "set them in config.yaml or OGTOOL_API_ID / OGTOOL_API_HASH"
            )
        return self.api_id, self.api_hash

    def session_path(self, label: str) -> Path:
        return self.session_dir / f"{label}.session"


def load_task(path: str | Path, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read a task file and merge it over the global settings."""
    task = load_yaml(path)
    return merge(base or {}, task)
