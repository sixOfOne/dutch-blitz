"""Clerk session checks.

The browser sends the session JWT in the ``__session`` cookie, or in an
``Authorization: Bearer`` header. The signature is checked against Clerk's
public JWKS. ``CLERK_SECRET_KEY`` has to be set so a half-configured server
fails closed, but the secret is not used to verify the token and is never
sent to the browser.
"""

from collections.abc import Callable
from typing import Any, Protocol

import jwt

from app.config import (
    clerk_authorized_parties,
    clerk_dev_bypass,
    clerk_dev_user_id,
    clerk_issuer,
    clerk_jwks_url,
    clerk_secret_key,
)
from app.errors import AuthError

SESSION_COOKIE = "__session"
MAX_TOKEN_LENGTH = 8192

KeyLoader = Callable[[str], object]


class CurrentUser:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id


class SessionVerifier(Protocol):
    """Anything the app can ask ``who is this request``."""

    def verify(self, token: str) -> CurrentUser: ...


class DevBypassVerifier:
    """Treat every request as one shared user. Off unless CLERK_DEV_BYPASS is set."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id or "dev-user"

    def verify(self, token: str) -> CurrentUser:
        del token
        return CurrentUser(self.user_id)


class ClerkTokenVerifier:
    def __init__(
        self,
        *,
        jwks_url: str,
        issuer: str,
        secret_key: str,
        authorized_parties: tuple[str, ...] = (),
        key_loader: KeyLoader | None = None,
    ) -> None:
        self.jwks_url = jwks_url
        self.issuer = issuer
        self.secret_key = secret_key
        self.authorized_parties = authorized_parties
        self._key_loader = key_loader
        self._jwk_client: jwt.PyJWKClient | None = None

    def verify(self, token: str) -> CurrentUser:
        if not self.secret_key or not self.jwks_url or not self.issuer:
            raise AuthError("Clerk is not configured")
        if not _looks_like_jwt(token):
            raise AuthError("Sign in to continue.")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError("Sign in to continue.") from exc
        if header.get("alg") != "RS256":
            raise AuthError("Sign in to continue.")
        try:
            payload = jwt.decode(
                token,
                self._signing_key(token),
                algorithms=["RS256"],
                issuer=self.issuer,
                leeway=10,
                options={"verify_aud": False, "require": ["exp", "sub", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError("Sign in to continue.") from exc
        if self.authorized_parties:
            azp = payload.get("azp")
            if azp not in self.authorized_parties:
                raise AuthError("Sign in to continue.")
        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject.strip() or len(subject) > 200:
            raise AuthError("Sign in to continue.")
        return CurrentUser(subject.strip())

    def _signing_key(self, token: str) -> Any:
        if self._key_loader is not None:
            return self._key_loader(token)
        if self._jwk_client is None:
            self._jwk_client = jwt.PyJWKClient(self.jwks_url, cache_keys=True)
        return self._jwk_client.get_signing_key_from_jwt(token).key


def verifier_from_env() -> DevBypassVerifier | ClerkTokenVerifier:
    if clerk_dev_bypass():
        return DevBypassVerifier(clerk_dev_user_id())
    return ClerkTokenVerifier(
        jwks_url=clerk_jwks_url(),
        issuer=clerk_issuer(),
        secret_key=clerk_secret_key(),
        authorized_parties=clerk_authorized_parties(),
    )


def _looks_like_jwt(token: str) -> bool:
    if not token or len(token) > MAX_TOKEN_LENGTH or token.count(".") != 2:
        return False
    return all(part for part in token.split("."))
