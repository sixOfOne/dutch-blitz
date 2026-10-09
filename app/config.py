"""Runtime settings.

The database path, bind address, and port come from the environment so a later
deploy can change them without code changes. Nothing here starts infrastructure.
"""

import os

DEFAULT_DB_PATH = "data/dutch_blitz.sqlite"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


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
