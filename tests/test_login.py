"""Live login, driven by files and a mock client: no network, no prompts."""

from __future__ import annotations

from pathlib import Path

import pytest

from ogtool.accounts import AccountPool
from ogtool.cli import run
from ogtool.login import (
    CREDENTIAL_ENV,
    Credentials,
    LoginConfig,
    mask_api_id,
    mask_phone,
    parse_credentials,
    poll_file,
    resolve_credentials,
)
from ogtool.login import login as do_login

API_ID = 1234567
API_HASH = "cafebabe" * 4  # 32 hex characters
BLOB = f"Officegram api_id {API_ID} api_hash {API_HASH} extra label text"


class PhoneCodeInvalidError(Exception):
    pass


class PhoneCodeExpiredError(Exception):
    pass


class SessionPasswordNeededError(Exception):
    pass


class FloodWaitError(Exception):
    def __init__(self, seconds: int = 0) -> None:
        super().__init__("flood")
        self.seconds = seconds


class SentCode:
    phone_code_hash = "hash-1"


class Me:
    id = 4242
    username = "ann"
    phone = "+8613800000000"
    restricted = False


class FakeClient:
    """Answers one scripted action per ``sign_in`` call."""

    def __init__(self, actions: list[object] | None = None,
                 send_error: BaseException | None = None) -> None:
        self.actions = list(actions or [])
        self.send_error = send_error
        self.calls: list[dict[str, object]] = []
        self.connected = False
        self.disconnected = False
        self.me: object = Me()

    def connect(self) -> None:
        self.connected = True

    def send_code_request(self, phone: str) -> SentCode:
        if self.send_error is not None:
            raise self.send_error
        self.phone = phone
        return SentCode()

    def sign_in(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        action = self.actions.pop(0) if self.actions else None
        if isinstance(action, BaseException):
            raise action
        return self.me

    def get_me(self) -> object:
        return self.me

    def disconnect(self) -> None:
        self.disconnected = True


class FakeTime:
    """A clock that fires scheduled file writes as the poller sleeps."""

    def __init__(self, writes: dict[float, object] | None = None) -> None:
        self.now = 0.0
        self.writes = dict(writes or {})

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))
        due = sorted(at for at in self.writes if at <= self.now)
        for at in due:
            action = self.writes.pop(at)
            action()  # type: ignore[operator]


def config_for(tmp_path: Path, **overrides) -> LoginConfig:
    values = {
        "label": "demo1",
        "phone": "+8613800000000",
        "code_file": str(tmp_path / "code.txt"),
        "password_file": str(tmp_path / "pw.txt"),
        "timeout": 10.0,
        "interval": 1.0,
    }
    values.update(overrides)
    return LoginConfig(**values)


def run_login(tmp_path, settings, store, fake: FakeClient,
              config: LoginConfig | None = None,
              fake_time: FakeTime | None = None) -> int:
    clock = fake_time or FakeTime()
    return do_login(config or config_for(tmp_path), settings, store,
                    client_factory=lambda _path, _creds: fake,
                    clock=clock.clock, sleep=clock.sleep)


@pytest.fixture(autouse=True)
def clean_credential_env(monkeypatch):
    for name in (CREDENTIAL_ENV, "TG_API_CREDENTIALS", "TG_API_ID",
                 "TG_API_HASH", "OGTOOL_API_ID", "OGTOOL_API_HASH"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv(CREDENTIAL_ENV, BLOB)
    return BLOB


def test_a_late_code_file_still_logs_in(tmp_path, settings, store, creds,
                                        capsys):
    code = tmp_path / "code.txt"
    fake = FakeClient()
    time = FakeTime({2.0: lambda: code.write_text("55555\n")})
    assert run_login(tmp_path, settings, store, fake, fake_time=time) == 0
    output = capsys.readouterr().out
    assert "CODE_SENT" in output
    assert "LOGIN_OK user_id=4242 username=@ann phone=+8613****" in output
    assert fake.calls == [{"phone": "+8613800000000", "code": "55555",
                           "phone_code_hash": "hash-1"}]
    assert fake.disconnected
    # The code is consumed once, and the row plus the profile land in the store.
    assert not code.exists()
    account = store.get_account("demo1")
    assert account is not None and account.status == "ok"
    assert account.session == str(settings.session_path("demo1"))
    profile = store.get_profile("demo1")
    assert profile is not None and profile.user_id == "4242"


def test_the_blob_parses_and_never_leaks(tmp_path, settings, store, creds,
                                         capsys):
    fake = FakeClient()
    time = FakeTime({1.0: lambda: (tmp_path / "code.txt").write_text("1\n")})
    assert run_login(tmp_path, settings, store, fake, fake_time=time) == 0
    output = capsys.readouterr()
    combined = output.out + output.err
    assert BLOB not in combined
    assert API_HASH not in combined
    assert str(API_ID) not in combined
    assert "api_id=12***" in combined


def test_2fa_reads_the_password_file(tmp_path, settings, store, creds, capsys):
    fake = FakeClient([SessionPasswordNeededError()])
    time = FakeTime({
        1.0: lambda: (tmp_path / "code.txt").write_text("77777\n"),
        2.0: lambda: (tmp_path / "pw.txt").write_text("hunter2\n"),
    })
    assert run_login(tmp_path, settings, store, fake, fake_time=time) == 0
    output = capsys.readouterr().out
    assert "PASSWORD_NEEDED" in output
    assert "LOGIN_OK" in output
    assert fake.calls[1] == {"password": "hunter2"}
    assert not (tmp_path / "pw.txt").exists()


def test_two_bad_codes_then_a_good_one(tmp_path, settings, store, creds,
                                       capsys):
    fake = FakeClient([PhoneCodeInvalidError(), PhoneCodeInvalidError()])
    code = tmp_path / "code.txt"
    time = FakeTime({
        1.0: lambda: code.write_text("111\n"),
        2.0: lambda: code.write_text("222\n"),
        3.0: lambda: code.write_text("333\n"),
    })
    assert run_login(tmp_path, settings, store, fake, fake_time=time) == 0
    output = capsys.readouterr().out
    assert output.count("CODE_INVALID") == 2
    assert "LOGIN_OK" in output
    assert [call["code"] for call in fake.calls] == ["111", "222", "333"]


def test_three_bad_codes_end_in_login_failed(tmp_path, settings, store, creds,
                                             capsys):
    fake = FakeClient([PhoneCodeInvalidError(), PhoneCodeInvalidError(),
                       PhoneCodeInvalidError()])
    code = tmp_path / "code.txt"
    time = FakeTime({
        1.0: lambda: code.write_text("111\n"),
        2.0: lambda: code.write_text("222\n"),
        3.0: lambda: code.write_text("333\n"),
    })
    assert run_login(tmp_path, settings, store, fake, fake_time=time) == 1
    output = capsys.readouterr().out
    assert output.count("CODE_INVALID") == 3
    assert "LOGIN_FAILED" in output
    assert "LOGIN_OK" not in output
    assert store.get_account("demo1") is None


def test_no_code_before_the_timeout(tmp_path, settings, store, creds, capsys):
    time = FakeTime()
    assert run_login(tmp_path, settings, store, FakeClient(), fake_time=time,
                     config=config_for(tmp_path, timeout=3.0)) == 1
    output = capsys.readouterr().out
    assert "TIMEOUT_NO_CODE" in output
    assert time.now >= 3.0


def test_no_password_before_the_timeout(tmp_path, settings, store, creds,
                                        capsys):
    fake = FakeClient([SessionPasswordNeededError()])
    time = FakeTime({1.0: lambda: (tmp_path / "code.txt").write_text("1\n")})
    config = config_for(tmp_path, timeout=3.0)
    assert run_login(tmp_path, settings, store, fake, fake_time=time,
                     config=config) == 1
    output = capsys.readouterr().out
    assert "PASSWORD_NEEDED" in output
    assert "TIMEOUT_NO_PASSWORD" in output


def test_an_expired_code_is_its_own_line(tmp_path, settings, store, creds,
                                         capsys):
    fake = FakeClient([PhoneCodeExpiredError()])
    time = FakeTime({1.0: lambda: (tmp_path / "code.txt").write_text("1\n")})
    assert run_login(tmp_path, settings, store, fake, fake_time=time) == 1
    assert "PHONE_CODE_EXPIRED" in capsys.readouterr().out


def test_a_flood_wait_reports_the_remaining_seconds(tmp_path, settings, store,
                                                    creds, capsys):
    fake = FakeClient(send_error=FloodWaitError(42))
    assert run_login(tmp_path, settings, store, fake) == 1
    assert "FLOOD_WAIT_42" in capsys.readouterr().out


def test_missing_credentials_exit_2_without_printing_anything(tmp_path,
                                                              settings, store,
                                                              capsys):
    assert run_login(tmp_path, settings, store, FakeClient()) == 2
    output = capsys.readouterr()
    combined = output.out + output.err
    assert "api credentials not found" in combined
    assert BLOB not in combined


def test_login_needs_a_label_phone_and_code_file(tmp_path, settings, store,
                                                 capsys):
    fake = FakeClient()
    assert do_login(config_for(tmp_path, label=""), settings, store,
                    client_factory=lambda _p, _c: fake) == 2
    assert "needs --label and --phone" in capsys.readouterr().out
    assert do_login(config_for(tmp_path, code_file=""), settings, store,
                    client_factory=lambda _p, _c: fake) == 2
    assert "needs --code-file" in capsys.readouterr().out
    assert not fake.connected


def test_dry_run_connects_to_nothing(tmp_path, settings, store, capsys):
    fake = FakeClient()
    assert do_login(config_for(tmp_path, dry_run=True), settings, store,
                    client_factory=lambda _p, _c: fake) == 0
    assert "dry-run: would log in demo1" in capsys.readouterr().out
    assert not fake.connected


def test_parse_credentials_handles_labels_and_plain_values():
    assert parse_credentials(BLOB) == Credentials(API_ID, API_HASH)
    assert parse_credentials(f"{API_ID} {API_HASH}") == Credentials(API_ID,
                                                                    API_HASH)
    labelled = "api_id: 987654\napi_hash: " + "ab" * 16
    assert parse_credentials(labelled) == Credentials(987654, "ab" * 16)
    assert parse_credentials("") is None
    assert parse_credentials("only an id 12345678") is None
    assert parse_credentials("only a hash " + "cd" * 16) is None


def test_resolve_credentials_prefers_the_blob(settings, monkeypatch):
    monkeypatch.setenv(CREDENTIAL_ENV, BLOB)
    monkeypatch.setenv("TG_API_ID", "999999")
    monkeypatch.setenv("TG_API_HASH", "ef" * 16)
    assert resolve_credentials(settings) == Credentials(API_ID, API_HASH)


def test_resolve_credentials_falls_back_to_plain_env(settings, monkeypatch):
    monkeypatch.setenv("TG_API_ID", "999999")
    monkeypatch.setenv("TG_API_HASH", "ef" * 16)
    assert resolve_credentials(settings) == Credentials(999999, "ef" * 16)


def test_the_masks_never_show_the_whole_value():
    assert mask_api_id(API_ID) == "12***"
    assert mask_phone("+8613800000000") == "+8613****"
    assert mask_phone("") == "***"


def test_poll_file_consumes_what_it_reads(tmp_path):
    code = tmp_path / "code.txt"
    code.write_text("\n  424242  \n")
    assert poll_file(code, 0.0) == "424242"
    assert not code.exists()
    assert poll_file(code, 0.0) is None


def test_accounts_test_live_reports_the_real_identity(settings, store, creds):
    session = settings.session_path("seat1")
    session.write_bytes(b"")
    from ogtool.store import Account

    store.add_account(Account("seat1", phone="+8613800000000",
                              session=str(session)))

    factory = lambda _path, _creds: FakeClient()  # noqa: E731
    rows = AccountPool(settings, store).test(["seat1"], live=True,
                                             client_factory=factory)
    assert rows == [("seat1", True,
                     "user_id=4242 username=@ann phone=+8613**** "
                     "restricted=no")]
    assert store.get_account("seat1").status == "ok"


def test_accounts_test_live_still_reports_a_missing_session(settings, store):
    from ogtool.store import Account

    store.add_account(Account("gone", session=str(settings.session_dir / "no")))
    rows = AccountPool(settings, store).test(["gone"], live=True,
                                             client_factory=lambda _p, _c: None)
    assert rows[0][1] is False
    assert "SESSION_NOT_FOUND" in rows[0][2]


def test_an_unexpected_error_is_one_line_and_exit_1(tmp_path, settings, store,
                                                    creds, capsys):
    class Broken(FakeClient):
        def connect(self) -> None:
            raise RuntimeError("boom with a value that must not be printed")

    code = run_login(tmp_path, settings, store, Broken())
    output = capsys.readouterr()
    assert code == 1
    assert "LOGIN_FAILED RuntimeError" in output.out
    assert "boom" not in output.out + output.err


def test_the_configured_proxy_reaches_the_client(tmp_path, settings, store,
                                                 creds, monkeypatch):
    from ogtool.config import ProxyConfig

    settings.proxy = ProxyConfig(kind="socks5", host="127.0.0.1", port=1080)
    seen: dict[str, object] = {}

    def fake_build(path, creds, proxy=None):
        seen["proxy"] = proxy
        return FakeClient()

    monkeypatch.setattr("ogtool.login.build_client", fake_build)
    time = FakeTime({1.0: lambda: (tmp_path / "code.txt").write_text("1\n")})
    assert do_login(config_for(tmp_path), settings, store,
                    clock=time.clock, sleep=time.sleep) == 0
    assert getattr(seen["proxy"], "port", 0) == 1080


def test_require_credentials_falls_back_to_the_blob(settings, monkeypatch):
    monkeypatch.setenv(CREDENTIAL_ENV, BLOB)
    assert settings.require_credentials() == (API_ID, API_HASH)


def test_require_credentials_prefers_the_config(settings, monkeypatch):
    monkeypatch.setenv(CREDENTIAL_ENV, BLOB)
    settings.api_id = 111111
    settings.api_hash = "ab" * 16
    assert settings.require_credentials() == (111111, "ab" * 16)


def test_require_credentials_without_any_source_is_an_error(settings):
    from ogtool.config import ConfigError

    with pytest.raises(ConfigError):
        settings.require_credentials()


def test_accounts_test_live_passes_a_proxy_tuple_not_a_string(settings, store,
                                                              creds, monkeypatch):
    from ogtool.config import ProxyConfig
    from ogtool.store import Account

    settings.proxy = ProxyConfig(kind="socks5", host="127.0.0.1", port=12888)
    session = settings.session_path("p1")
    session.write_bytes(b"")
    store.add_account(Account("p1", session=str(session)))
    seen: dict[str, object] = {}

    def fake_build(path, credentials, proxy=None):
        seen["proxy"] = proxy
        return FakeClient()

    monkeypatch.setattr("ogtool.login.build_client", fake_build)
    rows = AccountPool(settings, store).test(["p1"], live=True)
    assert rows[0][1] is True
    assert isinstance(seen["proxy"], ProxyConfig)
    assert seen["proxy"].to_telethon() == (
        "socks5", "127.0.0.1", 12888, True, None, None)


def test_the_account_row_still_stores_a_string(settings, store):
    from ogtool.config import ProxyConfig

    settings.proxy = ProxyConfig(kind="socks5", host="127.0.0.1", port=12888)
    account = AccountPool(settings, store).add_phone("+8613800000000", "row1")
    assert account.proxy == "socks5://127.0.0.1:12888"
    assert store.get_account("row1").proxy == "socks5://127.0.0.1:12888"


def test_the_cli_accepts_login_and_dry_runs_it(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("api_id: 0\napi_hash: ''\n", encoding="utf-8")
    code = run(["--config", str(config), "accounts", "login", "--label", "x",
                "--phone", "+8613800000000", "--code-file", "/tmp/none",
                "--dry-run"])
    assert code == 0
    assert "dry-run: would log in x" in capsys.readouterr().out
    assert run(["--config", str(config), "accounts", "login"]) == 2
