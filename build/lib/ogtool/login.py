"""Non interactive live login, plus the credential parsing it needs.

The live path is driven by a harness, not by a human: a code (and a 2FA
password, when the account has one) is dropped into a file by the caller and
this module polls for it.  Nothing here ever prints a credential: the ``api_id``
appears only as its first two digits, the phone only masked.

Credential sources, in order:

1. ``$DSH_SECRET_TG_API_CREDENTIALS`` (also ``$TG_API_CREDENTIALS``): one blob
   whose text may carry labels, so a labelled ``api_id`` / ``api_hash`` pair is
   looked for first and the first plausible values second.
2. ``$TG_API_ID`` + ``$TG_API_HASH`` (``$OGTOOL_*`` is accepted as well).
3. ``api_id`` / ``api_hash`` in ``config.yaml``.
"""

from __future__ import annotations

import contextlib
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ConfigError, Settings
from .store import Account, Profile, Store, utcnow

#: The secret the harness exports; the value mixes labels with the real pair.
CREDENTIAL_ENV = "DSH_SECRET_TG_API_CREDENTIALS"
CREDENTIAL_ENV_ALT = "TG_API_CREDENTIALS"
ID_ENVS = ("TG_API_ID", "OGTOOL_API_ID")
HASH_ENVS = ("TG_API_HASH", "OGTOOL_API_HASH")

#: How many fresh codes are accepted after the first one is rejected.
MAX_CODE_RETRIES = 2

_LABELLED_ID = re.compile(r"api[_ ]?id\D{0,20}?(\d{5,12})", re.IGNORECASE)
_LABELLED_HASH = re.compile(r"api[_ ]?hash\D{0,20}?([0-9a-fA-F]{32})",
                            re.IGNORECASE)
_ANY_ID = re.compile(r"(?<!\d)(\d{5,12})(?!\d)")
_ANY_HASH = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{32})(?![0-9a-fA-F])")


@dataclass(slots=True)
class Credentials:
    """The api pair, kept out of every printed string."""

    api_id: int
    api_hash: str


def parse_credentials(text: str) -> Credentials | None:
    """Pull a pair out of a blob that may also hold labels; never logs it."""
    if not text:
        return None
    identifier = _LABELLED_ID.search(text) or _ANY_ID.search(text)
    digest = _LABELLED_HASH.search(text) or _ANY_HASH.search(text)
    if not identifier or not digest:
        return None
    return Credentials(api_id=int(identifier.group(1)),
                       api_hash=digest.group(1))


def resolve_credentials(settings: Settings,
                        env: Mapping[str, str] | None = None) -> Credentials:
    """The blob first, then the plain env vars, then ``config.yaml``."""
    environ = os.environ if env is None else env
    for name in (CREDENTIAL_ENV, CREDENTIAL_ENV_ALT):
        found = parse_credentials(environ.get(name, ""))
        if found:
            return found
    identifier = next((environ[name] for name in ID_ENVS if environ.get(name)),
                      "")
    digest = next((environ[name] for name in HASH_ENVS if environ.get(name)),
                  "")
    if identifier and digest:
        try:
            return Credentials(api_id=int(identifier.strip()),
                               api_hash=digest.strip())
        except ValueError as error:
            raise ConfigError("$TG_API_ID is not a number") from error
    if settings.api_id and settings.api_hash:
        return Credentials(api_id=int(settings.api_id),
                           api_hash=str(settings.api_hash))
    raise ConfigError(
        "api credentials not found: set $DSH_SECRET_TG_API_CREDENTIALS "
        "(or $TG_API_ID/$TG_API_HASH, or api_id/api_hash in config.yaml)"
    )


def mask_api_id(api_id: int) -> str:
    """``1234567`` -> ``12***``; the full value never leaves this module."""
    return f"{str(int(api_id))[:2]}***"


def mask_phone(phone: str) -> str:
    """``+8613800000000`` -> ``+8613****``."""
    text = str(phone or "")
    if len(text) < 6:
        return "***"
    return f"{text[:5]}****"


@dataclass(slots=True)
class LoginConfig:
    """What one non interactive login needs, all from the command line."""

    label: str = ""
    phone: str = ""
    code_file: str = ""
    password_file: str = ""
    timeout: float = 300.0
    interval: float = 1.0
    dry_run: bool = False

    def session_path(self, settings: Settings) -> Path:
        return settings.session_path(self.label or self.phone)


def build_client(session_path: str, credentials: Credentials,
                 proxy: Any = None) -> Any:
    """The real client; imported lazily so a dry-run never needs telethon."""
    from telethon.sync import TelegramClient

    options: dict[str, Any] = {}
    if proxy is not None:
        options["proxy"] = proxy.to_telethon()
    return TelegramClient(session_path, credentials.api_id,
                          credentials.api_hash, **options)


def _read_and_consume(path: str | Path) -> str | None:
    """First line of the file, deleted at once so a value is used one time."""
    target = Path(path).expanduser()
    if not target.exists():
        return None
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    finally:
        target.unlink(missing_ok=True)
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return None


def poll_file(path: str | Path, timeout: float, interval: float = 1.0,
              clock: Callable[[], float] = time.monotonic,
              sleep: Callable[[float], None] = time.sleep) -> str | None:
    """Wait for a file to appear; ``None`` when the timeout runs out."""
    deadline = clock() + max(0.0, float(timeout))
    while True:
        value = _read_and_consume(path)
        if value:
            return value
        remaining = deadline - clock()
        if remaining <= 0:
            return None
        sleep(min(max(0.01, float(interval)), remaining))


def _kind(error: BaseException) -> str:
    return type(error).__name__


def _flood_seconds(error: BaseException) -> int:
    seconds = getattr(error, "seconds", 0) or 0
    try:
        return int(seconds)
    except (TypeError, ValueError):
        return 0


def login(config: LoginConfig, settings: Settings, store: Store,
          client_factory: Callable[[str, Credentials], Any] | None = None,
          clock: Callable[[], float] = time.monotonic,
          sleep: Callable[[float], None] = time.sleep) -> int:
    """Run the whole login; returns the process exit code."""
    if not config.label or not config.phone:
        print("ERROR: login needs --label and --phone")
        return 2
    if not config.code_file:
        print("ERROR: login needs --code-file (the code is read from it)")
        return 2
    if config.dry_run:
        print(f"dry-run: would log in {config.label} and poll "
              f"{config.code_file} for up to {config.timeout:.0f}s")
        return 0

    try:
        credentials = resolve_credentials(settings)
    except ConfigError as error:
        print(f"ERROR: {error}")
        return 2
    print(f"credentials: api_id={mask_api_id(credentials.api_id)}")

    session = config.session_path(settings)
    factory = client_factory or (
        lambda path, creds: build_client(path, creds, settings.proxy))
    try:
        client = factory(str(session), credentials)
    except Exception as error:  # noqa: BLE001 - class name only, never values
        print(f"LOGIN_FAILED {_kind(error)}")
        return 1
    try:
        client.connect()
        try:
            sent = client.send_code_request(config.phone)
        except Exception as error:  # noqa: BLE001 - reported as a code line
            return _report_failure(error)
        code_hash = str(getattr(sent, "phone_code_hash", "") or "")
        print("CODE_SENT", flush=True)

        retries = 0
        while True:
            code = poll_file(config.code_file, config.timeout, config.interval,
                             clock=clock, sleep=sleep)
            if not code:
                print("TIMEOUT_NO_CODE")
                return 1
            try:
                client.sign_in(phone=config.phone, code=code,
                               phone_code_hash=code_hash)
                break
            except Exception as error:  # noqa: BLE001 - classified below
                name = _kind(error)
                if name == "SessionPasswordNeededError":
                    code = _password_stage(config, client, clock, sleep)
                    if code:
                        print(code)
                        return 1
                    break
                if name == "PhoneCodeInvalidError":
                    retries += 1
                    print("CODE_INVALID", flush=True)
                    if retries > MAX_CODE_RETRIES:
                        print("LOGIN_FAILED")
                        return 1
                    continue
                if name == "PhoneCodeExpiredError":
                    print("PHONE_CODE_EXPIRED")
                    return 1
                return _report_failure(error)

        me = client.get_me()
        if me is None:
            print("LOGIN_FAILED")
            return 1
        remember(store, config, session, me)
        print(f"LOGIN_OK user_id={getattr(me, 'id', '')} "
              f"username={_username(me)} phone={mask_phone(config.phone)}")
        return 0
    except Exception as error:  # noqa: BLE001 - safety net for connect/get_me
        return _report_failure(error)
    finally:
        with contextlib.suppress(Exception):
            client.disconnect()


def _password_stage(config: LoginConfig, client: Any,
                    clock: Callable[[], float],
                    sleep: Callable[[float], None]) -> str | None:
    """Ask the caller for the 2FA password; ``None`` means signed in."""
    print("PASSWORD_NEEDED", flush=True)
    if not config.password_file:
        return "TIMEOUT_NO_PASSWORD"
    password = poll_file(config.password_file, config.timeout, config.interval,
                         clock=clock, sleep=sleep)
    if not password:
        return "TIMEOUT_NO_PASSWORD"
    try:
        client.sign_in(password=password)
    except Exception as error:  # noqa: BLE001 - the class name is the report
        return f"LOGIN_FAILED {_kind(error)}"
    return None


def _report_failure(error: BaseException) -> int:
    """One line per failure family; never the message, it may echo a value."""
    name = _kind(error)
    if name == "FloodWaitError":
        print(f"FLOOD_WAIT_{_flood_seconds(error)}")
        return 1
    if name == "PhoneCodeExpiredError":
        print("PHONE_CODE_EXPIRED")
        return 1
    print(f"LOGIN_FAILED {name}")
    return 1


def _username(me: Any) -> str:
    name = getattr(me, "username", "") or ""
    return f"@{name}" if name else "-"


def remember(store: Store, config: LoginConfig, session: Path, me: Any) -> None:
    """Write the pool row and the profile the report pages read."""
    store.add_account(Account(
        label=config.label,
        phone=config.phone,
        session=str(session),
        status="ok",
        notes="logged in",
    ))
    store.save_profile(Profile(
        label=config.label,
        user_id=str(getattr(me, "id", "") or ""),
        username=str(getattr(me, "username", "") or ""),
        phone=config.phone,
        last_active=utcnow(),
    ))


__all__ = [
    "CREDENTIAL_ENV",
    "Credentials",
    "LoginConfig",
    "MAX_CODE_RETRIES",
    "build_client",
    "login",
    "mask_api_id",
    "mask_phone",
    "parse_credentials",
    "poll_file",
    "remember",
    "resolve_credentials",
]
