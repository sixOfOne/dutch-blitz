"""Auth enforcement and per-user isolation. Token checks are stubbed."""

import json
import sqlite3
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.auth import ClerkTokenVerifier
from app.errors import AuthError
from app.main import create_app
from tests.fakes import FakeVerifier


def _anon(tmp_path):
    application = create_app(str(tmp_path / "auth.sqlite"), verifier=FakeVerifier())
    return TestClient(application)


def test_game_pages_and_api_reject_anonymous_requests(tmp_path):
    with _anon(tmp_path) as client:
        home = client.get("/")
        assert home.status_code == 401
        assert "Sign in" in home.text
        assert "Ada" not in home.text
        missing = client.get("/games/999")
        assert missing.status_code == 401
        assert "Game not found" not in missing.text
        created = client.post(
            "/games",
            data={"target_score": "75", "player_name": ["Ada", "Bo"]},
        )
        assert created.status_code == 401
        for path in ("/games/1/rounds", "/games/1/rounds/last", "/games/1/undo"):
            denied = client.post(path, data={})
            assert denied.status_code == 401
        api = client.get("/", headers={"Accept": "application/json"})
        assert api.status_code == 401
        assert api.json() == {"detail": "Sign in to continue."}
        assert client.get("/static/style.css").status_code == 200
        assert client.get("/static/auth.js").status_code == 200

    with sqlite3.connect(tmp_path / "auth.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0


def test_invalid_token_is_rejected_and_bearer_selects_the_user(tmp_path):
    application = create_app(str(tmp_path / "auth.sqlite"), verifier=FakeVerifier())
    with TestClient(application) as client:
        client.cookies.set("__session", "not-a-session")
        assert client.get("/").status_code == 401
        client.cookies.clear()
        created = client.post(
            "/games",
            data={"target_score": "10", "player_name": ["Ada", "Bo"]},
            headers={"Authorization": "Bearer user:user_a"},
            follow_redirects=False,
        )
        assert created.status_code == 303
        own = client.get("/", headers={"Authorization": "Bearer user:user_a"})
        assert "Ada" in own.text
        other = client.get("/", headers={"Authorization": "Bearer user:user_b"})
        assert other.status_code == 200
        assert "Ada" not in other.text
        game_id = created.headers["location"].rsplit("/", 1)[-1]
        hidden = client.get(f"/games/{game_id}", headers={"Authorization": "Bearer user:user_b"})
        assert hidden.status_code == 404
        assert "Ada" not in hidden.text
        assert "Game not found" in hidden.text


def test_users_cannot_score_or_undo_each_others_games(tmp_path):
    application = create_app(str(tmp_path / "auth.sqlite"), verifier=FakeVerifier())
    with TestClient(application) as client:
        created = client.post(
            "/games",
            data={"target_score": "10", "player_name": ["Ada", "Bo"]},
            headers={"Authorization": "Bearer user:user_a"},
            follow_redirects=False,
        )
        location = created.headers["location"]
        page = client.get(location, headers={"Authorization": "Bearer user:user_a"})
        ada, bo = _player_ids(page.text)
        saved = client.post(
            f"{location}/rounds",
            data={
                f"dutch_{ada}": "4",
                f"blitz_{ada}": "0",
                f"dutch_{bo}": "1",
                f"blitz_{bo}": "0",
            },
            headers={"Authorization": "Bearer user:user_a"},
            follow_redirects=False,
        )
        assert saved.status_code == 303
        for path in (f"{location}/rounds", f"{location}/rounds/last", f"{location}/undo"):
            denied = client.post(
                path,
                data={
                    f"dutch_{ada}": "9",
                    f"blitz_{ada}": "0",
                    f"dutch_{bo}": "9",
                    f"blitz_{bo}": "0",
                },
                headers={"Authorization": "Bearer user:user_b"},
            )
            assert denied.status_code == 404
            assert "Ada" not in denied.text
        still = client.get(location, headers={"Authorization": "Bearer user:user_a"})
        assert "Ada leads with 4" in still.text


def test_sign_in_page_includes_the_publishable_key_only(monkeypatch, tmp_path):
    monkeypatch.delenv("CLERK_DEV_BYPASS", raising=False)
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_test_example")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_example")
    monkeypatch.setenv("CLERK_FRONTEND_API", "example.clerk.accounts.dev")
    application = create_app(str(tmp_path / "auth.sqlite"))
    with TestClient(application) as client:
        page = client.get("/")
    assert page.status_code == 401
    assert "pk_test_example" in page.text
    assert "example.clerk.accounts.dev" in page.text
    assert "data-clerk-sign-in" in page.text
    assert "sk_test_example" not in page.text


def test_dev_bypass_is_one_shared_account_and_hides_clerk(monkeypatch, tmp_path):
    monkeypatch.setenv("CLERK_DEV_BYPASS", "true")
    monkeypatch.setenv("CLERK_DEV_USER_ID", "shared")
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_test_example")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_example")
    application = create_app(str(tmp_path / "auth.sqlite"))
    with TestClient(application) as client:
        first = client.post(
            "/games",
            data={"target_score": "75", "player_name": ["Ada", "Bo"]},
            follow_redirects=False,
        )
        second = client.post(
            "/games",
            data={"target_score": "75", "player_name": ["Cy", "Di"]},
            headers={"Authorization": "Bearer user:someone-else"},
            follow_redirects=False,
        )
        assert first.status_code == 303
        assert second.status_code == 303
        home = client.get("/")
    assert home.status_code == 200
    assert "Development bypass is on" in home.text
    assert "Start game" in home.text
    assert "Ada" in home.text
    assert "Cy" in home.text
    assert "pk_test_example" not in home.text
    assert "sk_test_example" not in home.text


def _player_ids(html: str) -> tuple[str, str]:
    import re
    ids = re.findall(r'name="dutch_(\d+)"', html)
    assert len(ids) >= 2
    return ids[0], ids[1]


def _private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(private_key, **overrides) -> str:
    now = int(time.time())
    payload = {
        "sub": "user_abc",
        "iss": "https://example.clerk.accounts.dev",
        "exp": now + 120,
        "azp": "http://127.0.0.1:8000",
    }
    payload.update(overrides)
    return jwt.encode(payload, private_key, algorithm="RS256")


def _verifier(public_key, **kwargs):
    calls = []

    def loader(token: str):
        calls.append(token)
        return public_key

    verifier = ClerkTokenVerifier(
        jwks_url=kwargs.get("jwks_url", "https://example.clerk.accounts.dev/.well-known/jwks.json"),
        issuer=kwargs.get("issuer", "https://example.clerk.accounts.dev"),
        secret_key=kwargs.get("secret_key", "sk_test_example"),
        authorized_parties=kwargs.get("authorized_parties", ()),
        key_loader=loader,
    )
    return verifier, calls


def test_clerk_verifier_accepts_a_local_rs256_token():
    private_key = _private_key()
    verifier, calls = _verifier(private_key.public_key())
    user = verifier.verify(_token(private_key))
    assert user.user_id == "user_abc"
    assert calls


def test_clerk_verifier_rejects_bad_tokens_without_a_network_key_fetch():
    private_key = _private_key()
    public_key = private_key.public_key()
    verifier, calls = _verifier(public_key)

    with pytest.raises(AuthError, match="Sign in"):
        verifier.verify(_token(private_key, exp=int(time.time()) - 60))
    with pytest.raises(AuthError, match="Sign in"):
        verifier.verify(_token(private_key, iss="https://evil.example"))
    with pytest.raises(AuthError, match="Sign in"):
        verifier.verify(_token(private_key, sub=""))
    header = jwt.encode({"sub": "user_abc"}, private_key, algorithm="RS256").split(".")[0]
    # Rebuild a token whose header claims alg none. The signature is irrelevant.
    body = jwt.encode(
        {"sub": "user_abc", "iss": "https://example.clerk.accounts.dev", "exp": int(time.time()) + 60},
        private_key,
        algorithm="RS256",
    ).split(".")[1]
    none_header = _b64url({"alg": "none", "typ": "JWT"})
    with pytest.raises(AuthError, match="Sign in"):
        verifier.verify(f"{none_header}.{body}.x")
    assert header
    assert verifier._jwk_client is None

    unconfigured, unconfigured_calls = _verifier(public_key, secret_key="", jwks_url="", issuer="")
    with pytest.raises(AuthError, match="not configured"):
        unconfigured.verify(_token(private_key))
    with pytest.raises(AuthError, match="not configured"):
        unconfigured.verify("user:abc")
    assert unconfigured_calls == []
    assert calls  # only the well-formed RS256 attempts reached the loader


def test_azp_is_checked_only_when_parties_are_configured():
    private_key = _private_key()
    open_verifier, _calls = _verifier(private_key.public_key())
    assert open_verifier.verify(_token(private_key, azp="https://other.example")).user_id == "user_abc"

    limited, _limited_calls = _verifier(
        private_key.public_key(),
        authorized_parties=("http://127.0.0.1:8000",),
    )
    assert limited.verify(_token(private_key)).user_id == "user_abc"
    with pytest.raises(AuthError, match="Sign in"):
        limited.verify(_token(private_key, azp="https://other.example"))


def test_non_jwt_does_not_ask_for_a_signing_key():
    private_key = _private_key()
    verifier, calls = _verifier(private_key.public_key())
    with pytest.raises(AuthError, match="Sign in"):
        verifier.verify("user:abc")
    assert calls == []
    assert verifier._jwk_client is None


def _b64url(value: dict) -> str:
    import base64
    raw = json.dumps(value).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
