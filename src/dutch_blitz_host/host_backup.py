"""Upload or restore the score database. This runs on the EC2 host.

The instance role is the only AWS credential. Clerk keys are not read here.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from dutch_blitz_host.backup import (
    BACKUP_PREFIX,
    backup_object_name,
    backup_sqlite,
    latest_backup_name,
    s3_cp_argv,
    s3_ls_argv,
    s3_uri,
)
from dutch_blitz_host.envfile import parse_dotenv

BACKUP_ENV = Path("/etc/dutch-blitz/backup.env")


def load_backup_env(path: Path = BACKUP_ENV) -> None:
    if not path.is_file():
        return
    for key, value in parse_dotenv(path.read_text(encoding="utf-8")).items():
        os.environ.setdefault(key, value)


def backup_database() -> int:
    load_backup_env()
    db_path = Path(os.environ.get("DUTCH_BLITZ_DB_PATH", "/var/lib/dutch-blitz/dutch_blitz.sqlite"))
    if not db_path.is_file():
        print("No local score database yet; nothing to upload.")
        return 0
    bucket = _bucket()
    name = backup_object_name(datetime.now(UTC))
    remote = s3_uri(bucket, BACKUP_PREFIX, name)
    with tempfile.TemporaryDirectory(prefix="dutch-blitz-backup-") as directory:
        snapshot = Path(directory) / name
        backup_sqlite(db_path, snapshot)
        _run(s3_cp_argv(str(snapshot), remote))
    print(f"Uploaded {remote}")
    return 0


def restore_database() -> int:
    load_backup_env()
    bucket = _bucket()
    result = subprocess.run(
        s3_ls_argv(bucket, BACKUP_PREFIX),
        check=False,
        text=True,
        capture_output=True,
    )
    # `aws s3 ls` exits 1 with no output when the prefix has no objects yet,
    # which is the normal state before the first game night's backup.
    if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
        print("No score backup in S3 yet.")
        return 0
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr
        )
    listing = result.stdout
    name = latest_backup_name(listing)
    if name is None:
        print("No score backup in S3 yet.")
        return 0
    remote = s3_uri(bucket, BACKUP_PREFIX, name)
    db_path = Path(os.environ.get("DUTCH_BLITZ_DB_PATH", "/var/lib/dutch-blitz/dutch_blitz.sqlite"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dutch-blitz-restore-") as directory:
        downloaded = Path(directory) / name
        _run(s3_cp_argv(remote, str(downloaded)))
        backup_sqlite(downloaded, db_path)
    print(f"Restored {remote}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["backup"]:
        return backup_database()
    if args == ["restore"]:
        return restore_database()
    print("Usage: dutch-blitz-backup backup|restore", file=sys.stderr)
    return 2


def _bucket() -> str:
    bucket = os.environ.get("BACKUP_BUCKET", "").strip()
    if not bucket:
        raise SystemExit("BACKUP_BUCKET is not set.")
    return bucket


def _run(argv: list[str]) -> None:
    completed = subprocess.run(argv, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
