"""Consistent SQLite snapshots and the S3 commands that move them.

The copy uses Python's sqlite3 backup API, which reads a live database
without copying a half-written page. Object names sort by time, so the
latest backup is the last name in lexicographic order.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BACKUP_PREFIX = "dutch-blitz"
NIGHT_STACK = "terraform"
BACKUP_STACK = "terraform/backup"


def backup_sqlite(source: Path, destination: Path) -> None:
    """Write a consistent snapshot of ``source`` to ``destination``."""
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    if partial.exists():
        partial.unlink()
    source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dest_conn = sqlite3.connect(partial)
        try:
            with dest_conn:
                source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()
    partial.replace(destination)


def backup_object_name(moment: datetime) -> str:
    """UTC timestamp key, for example ``20261010T131500Z.sqlite``."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y%m%dT%H%M%SZ.sqlite")


def s3_uri(bucket: str, prefix: str, name: str) -> str:
    cleaned_bucket = bucket.strip()
    cleaned_prefix = prefix.strip().strip("/")
    cleaned_name = name.strip().lstrip("/")
    if not cleaned_bucket or "/" in cleaned_bucket:
        raise ValueError("bucket name is missing")
    if not cleaned_name or "/" in cleaned_name:
        raise ValueError("object name must be a single path segment")
    return f"s3://{cleaned_bucket}/{cleaned_prefix}/{cleaned_name}"


def s3_cp_argv(local_path: str, remote_uri: str) -> list[str]:
    return ["aws", "s3", "cp", local_path, remote_uri]


def s3_ls_argv(bucket: str, prefix: str) -> list[str]:
    cleaned_prefix = prefix.strip().strip("/")
    return ["aws", "s3", "ls", f"s3://{bucket.strip()}/{cleaned_prefix}/"]


def latest_backup_name(listing: str) -> str | None:
    """Pick the newest ``*.sqlite`` name from ``aws s3 ls`` text.

    ``aws s3 ls`` prints the base name, not the prefix. Names from
    ``backup_object_name`` sort in time order.
    """
    names: list[str] = []
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[-1].endswith(".sqlite"):
            names.append(parts[-1])
    if not names:
        return None
    return sorted(names)[-1]


def destroy_plan(*, skip_backup: bool) -> list[str]:
    """Steps for tearing down a game night. The backup bucket is not one of them."""
    steps: list[str] = []
    if not skip_backup:
        steps.append("backup")
    steps.append(f"terraform-destroy:{NIGHT_STACK}")
    return steps
