"""Clerk settings for the host. Values are never logged by this module."""

from __future__ import annotations

from collections.abc import Mapping

ENV_FILE_MODE = "0600"
_TRUTHY = {"1", "true", "yes", "on"}
_DEPLOY_KEYS = (
    "CLERK_PUBLISHABLE_KEY",
    "CLERK_SECRET_KEY",
    "CLERK_DEV_BYPASS",
)


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse a ``.env`` file. Existing process variables are not touched here."""
    found: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        found[key] = value
    return found


def merged_env(environ: Mapping[str, str], dotenv_text: str | None) -> dict[str, str]:
    """Process environment wins over ``.env``."""
    merged = {key: environ[key] for key in _DEPLOY_KEYS if key in environ}
    if not dotenv_text:
        return merged
    parsed = parse_dotenv(dotenv_text)
    for key in _DEPLOY_KEYS:
        merged.setdefault(key, parsed.get(key, ""))
    return merged


def bypass_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUTHY


def assert_deployable(env: Mapping[str, str]) -> None:
    if bypass_enabled(env.get("CLERK_DEV_BYPASS")):
        raise SystemExit("CLERK_DEV_BYPASS is on. Turn it off before hosting a game night.")
    publishable = (env.get("CLERK_PUBLISHABLE_KEY") or "").strip()
    secret = (env.get("CLERK_SECRET_KEY") or "").strip()
    if not publishable or not secret:
        raise SystemExit(
            "Set CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY in the environment or .env."
        )
    _reject_multiline("CLERK_PUBLISHABLE_KEY", publishable)
    _reject_multiline("CLERK_SECRET_KEY", secret)


def deployment_env(
    *,
    publishable_key: str,
    secret_key: str,
    origin: str,
    db_path: str,
) -> str:
    """Root-owned env file contents. Bypass is always off on the host."""
    _reject_multiline("CLERK_PUBLISHABLE_KEY", publishable_key)
    _reject_multiline("CLERK_SECRET_KEY", secret_key)
    _reject_multiline("CLERK_AUTHORIZED_PARTIES", origin)
    if not origin.startswith("https://"):
        raise ValueError("authorized origin must be https")
    lines = [
        f"DUTCH_BLITZ_DB_PATH={db_path}",
        "DUTCH_BLITZ_HOST=127.0.0.1",
        "DUTCH_BLITZ_PORT=8000",
        f"CLERK_PUBLISHABLE_KEY={publishable_key}",
        f"CLERK_SECRET_KEY={secret_key}",
        "CLERK_DEV_BYPASS=0",
        f"CLERK_AUTHORIZED_PARTIES={origin}",
    ]
    return "\n".join(lines) + "\n"


def redact(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _reject_multiline(name: str, value: str) -> None:
    if not value.strip() or any(character in value for character in "\n\r\x00"):
        raise ValueError(f"{name} must be a single line")
