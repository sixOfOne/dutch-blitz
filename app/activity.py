"""Remember the last time a request reached the scorekeeper.

The idle-shutdown timer on a game-night host reads this file. Any use of
the site, including a refresh, moves the timestamp forward and the idle
clock starts over.

This file is not a control. Writing it cannot add time to the hard uptime
limit, and it cannot power the machine off. That limit is stored in a
root-owned state file the app user cannot change. Snoozing it requires
SSH. There is no URL for it.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

ACTIVITY_NAME = "last-activity"


def activity_path(db_path: str | None, override: str | None = None) -> Path | None:
    """Where the stamp goes.

    ``DUTCH_BLITZ_ACTIVITY_PATH`` wins when it is set. Otherwise the stamp
    sits next to the SQLite file. It is not part of the S3 snapshot. A new
    instance must not inherit last night's timestamp and look busy, or
    look idle since last week.
    """
    chosen = override
    if chosen is None:
        chosen = os.environ.get("DUTCH_BLITZ_ACTIVITY_PATH", "")
    chosen = chosen.strip()
    if chosen:
        return Path(chosen)
    if not db_path:
        return None
    return Path(db_path).parent / ACTIVITY_NAME


def note_activity(
    db_path: str | None,
    *,
    now: float | None = None,
    override: str | None = None,
) -> None:
    """Write the current time. Failures are ignored.

    A score request must still succeed when the stamp cannot be written.
    The uptime limit shuts the host down even if this file is never updated.
    """
    path = activity_path(db_path, override)
    if path is None:
        return
    moment = time.time() if now is None else now
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{moment:.3f}\n", encoding="utf-8")
    except OSError:
        return
