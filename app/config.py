"""Runtime settings.

The database path, bind address, port, and Clerk settings come from the
environment so a later deploy can change them without code changes. Nothing
here starts infrastructure.
"""

import base64
import os
from pathlib import Path

DEFAULT_DB_PATH = "data/dutch_blitz.sqlite"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_DEV_USER_ID = "dev-user"
_TRUTHY = {"1", "true", "yes", "on"}


def load_local_env() -> None:
    """Load a gitignored ``.env`` without overriding variables already set."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env", override=False)


load_local_env()


def db_path() -> str:
    return os.environ.get("DUTCH_BLITZ_DB_PATH", DEFAULT_DB_PATH)


def host() -> str:
    return os.environ.get("DUTCH_BLITZ_HOST", DEFAULT_HOST)


def port() -> int:
    raw = os.environ.get("DUTCH_BLITZ_PORT", str(DEFAULT_PORT))
    if not raw.isdigit() or not 1 <= int(raw) <= 65535:
        raise SystemExit(
            f"DUTCH_BLITZ_PORT must be an integer from 1 to 65535, got {raw!r}."
        )
    return int(raw)


def clerk_publishable_key() -> str:
    return os.environ.get("CLERK_PUBLISHABLE_KEY", "").strip()


def clerk_secret_key() -> str:
    return os.environ.get("CLERK_SECRET_KEY", "").strip()


def clerk_dev_bypass() -> bool:
    return os.environ.get("CLERK_DEV_BYPASS", "").strip().lower() in _TRUTHY


def clerk_dev_user_id() -> str:
    raw = os.environ.get("CLERK_DEV_USER_ID", "").strip()
    return raw or DEFAULT_DEV_USER_ID


def frontend_api_from_publishable_key(key: str) -> str | None:
    """Decode the Frontend API host stored in a Clerk publishable key.

    Development keys (``pk_test_``) and production keys (``pk_live_``) both
    encode ``base64(frontend_api_host + '$')``. The host is used as-is. A
    production key for this domain decodes to ``clerk.sixofonedev.com``.
    """
    encoded = ""
    for prefix in ("pk_test_", "pk_live_"):
        if key.startswith(prefix):
            encoded = key[len(prefix):]
            break
    if not encoded:
        return None
    padded = encoded + "=" * (-len(encoded) % 4)
    decoded = None
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            decoded = decoder(padded).decode("utf-8")
            break
        except (ValueError, UnicodeDecodeError):
            continue
    if not decoded or not decoded.endswith("$"):
        return None
    host_name = decoded[:-1].strip()
    if not host_name or any(character in host_name for character in " /\n\t"):
        return None
    return host_name


def clerk_frontend_api() -> str:
    explicit = os.environ.get("CLERK_FRONTEND_API", "").strip()
    if explicit:
        return explicit
    return frontend_api_from_publishable_key(clerk_publishable_key()) or ""


def clerk_issuer() -> str:
    explicit = os.environ.get("CLERK_ISSUER", "").strip().rstrip("/")
    if explicit:
        return explicit
    frontend = clerk_frontend_api()
    if not frontend:
        return ""
    return f"https://{frontend}"


def clerk_jwks_url() -> str:
    explicit = os.environ.get("CLERK_JWKS_URL", "").strip()
    if explicit:
        return explicit
    issuer = clerk_issuer()
    if not issuer:
        return ""
    return f"{issuer}/.well-known/jwks.json"


def clerk_authorized_parties() -> tuple[str, ...]:
    raw = os.environ.get("CLERK_AUTHORIZED_PARTIES", "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def clerk_browser_config() -> dict:
    """Public Clerk settings for the browser. The secret key is never included."""
    publishable = clerk_publishable_key()
    frontend = clerk_frontend_api()
    ready = bool(
        publishable
        and clerk_secret_key()
        and frontend
        and clerk_issuer()
        and clerk_jwks_url()
    )
    if not ready:
        return {"ready": False}
    return {
        "ready": True,
        "publishableKey": publishable,
        "frontendApi": frontend,
    }
