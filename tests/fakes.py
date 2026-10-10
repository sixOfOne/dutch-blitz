"""Test stand-in for Clerk. Production code does not accept this token shape."""

from app.auth import CurrentUser
from app.errors import AuthError


class FakeVerifier:
    def verify(self, token: str) -> CurrentUser:
        if not token.startswith("user:") or len(token) <= len("user:"):
            raise AuthError("Sign in to continue.")
        user_id = token[len("user:"):]
        if not user_id.strip() or len(user_id) > 200:
            raise AuthError("Sign in to continue.")
        return CurrentUser(user_id)
