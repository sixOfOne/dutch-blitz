"""Environment settings used to run the app."""

import base64

import pytest

from app.config import (
    clerk_authorized_parties,
    clerk_browser_config,
    clerk_dev_bypass,
    clerk_dev_user_id,
    clerk_issuer,
    clerk_jwks_url,
    db_path,
    frontend_api_from_publishable_key,
    host,
    port,
)


def _clear_clerk(monkeypatch):
    for name in (
        "CLERK_PUBLISHABLE_KEY",
        "CLERK_SECRET_KEY",
        "CLERK_FRONTEND_API",
        "CLERK_ISSUER",
        "CLERK_JWKS_URL",
        "CLERK_AUTHORIZED_PARTIES",
        "CLERK_DEV_BYPASS",
        "CLERK_DEV_USER_ID",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults(monkeypatch):
    monkeypatch.delenv("DUTCH_BLITZ_DB_PATH", raising=False)
    monkeypatch.delenv("DUTCH_BLITZ_HOST", raising=False)
    monkeypatch.delenv("DUTCH_BLITZ_PORT", raising=False)
    _clear_clerk(monkeypatch)
    assert db_path() == "data/dutch_blitz.sqlite"
    assert host() == "127.0.0.1"
    assert port() == 8000
    assert clerk_dev_bypass() is False
    assert clerk_dev_user_id() == "dev-user"
    assert clerk_browser_config() == {"ready": False}


def test_overrides(monkeypatch):
    monkeypatch.setenv("DUTCH_BLITZ_DB_PATH", "scores.sqlite")
    monkeypatch.setenv("DUTCH_BLITZ_HOST", "0.0.0.0")
    monkeypatch.setenv("DUTCH_BLITZ_PORT", "9000")
    assert db_path() == "scores.sqlite"
    assert host() == "0.0.0.0"
    assert port() == 9000


@pytest.mark.parametrize("raw", ["1", "65535", "8000"])
def test_good_ports(monkeypatch, raw):
    monkeypatch.setenv("DUTCH_BLITZ_PORT", raw)
    assert port() == int(raw)


@pytest.mark.parametrize("raw", ["0", "65536", "nope", "", "-1"])
def test_bad_ports(monkeypatch, raw):
    monkeypatch.setenv("DUTCH_BLITZ_PORT", raw)
    with pytest.raises(SystemExit):
        port()


def test_publishable_key_decodes_to_frontend_api_and_jwks(monkeypatch):
    _clear_clerk(monkeypatch)
    encoded = base64.b64encode(b"example.clerk.accounts.dev$").decode().rstrip("=")
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", f"pk_test_{encoded}")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_example")
    assert frontend_api_from_publishable_key(f"pk_test_{encoded}") == "example.clerk.accounts.dev"
    assert clerk_issuer() == "https://example.clerk.accounts.dev"
    assert clerk_jwks_url() == "https://example.clerk.accounts.dev/.well-known/jwks.json"
    assert clerk_browser_config()["publishableKey"].startswith("pk_test_")
    assert "sk_test_example" not in clerk_browser_config().values()


def test_live_publishable_key_uses_the_production_frontend_api(monkeypatch):
    _clear_clerk(monkeypatch)
    encoded = base64.b64encode(b"clerk.sixofonedev.com$").decode().rstrip("=")
    key = f"pk_live_{encoded}"
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", key)
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_live_example")
    assert frontend_api_from_publishable_key(key) == "clerk.sixofonedev.com"
    assert clerk_issuer() == "https://clerk.sixofonedev.com"
    assert clerk_jwks_url() == "https://clerk.sixofonedev.com/.well-known/jwks.json"
    assert clerk_browser_config()["frontendApi"] == "clerk.sixofonedev.com"
    assert "accounts.dev" not in clerk_issuer()
    assert "sk_live_example" not in clerk_browser_config().values()


def test_explicit_clerk_urls_and_parties(monkeypatch):
    _clear_clerk(monkeypatch)
    monkeypatch.setenv("CLERK_FRONTEND_API", "custom.example")
    monkeypatch.setenv("CLERK_ISSUER", "https://issuer.example/")
    monkeypatch.setenv("CLERK_JWKS_URL", "https://issuer.example/jwks")
    monkeypatch.setenv(
        "CLERK_AUTHORIZED_PARTIES", " http://127.0.0.1:8000, https://scores.example "
    )
    assert clerk_issuer() == "https://issuer.example"
    assert clerk_jwks_url() == "https://issuer.example/jwks"
    assert clerk_authorized_parties() == ("http://127.0.0.1:8000", "https://scores.example")


@pytest.mark.parametrize("raw", ["1", "true", "YES", "on"])
def test_dev_bypass_flag(monkeypatch, raw):
    _clear_clerk(monkeypatch)
    monkeypatch.setenv("CLERK_DEV_BYPASS", raw)
    assert clerk_dev_bypass() is True


def test_blank_dev_user_falls_back(monkeypatch):
    _clear_clerk(monkeypatch)
    monkeypatch.setenv("CLERK_DEV_USER_ID", "   ")
    assert clerk_dev_user_id() == "dev-user"
    monkeypatch.setenv("CLERK_DEV_USER_ID", "table")
    assert clerk_dev_user_id() == "table"
