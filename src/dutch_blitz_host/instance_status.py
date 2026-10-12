"""Decide whether `down` still needs an SSH backup.

A host that shut itself down is already gone by the time someone runs
`down`. The on-server timer uploaded a snapshot before it powered off.
`down` should skip SSH, then still delete the blitz DNS record and the
Terraform state. A failed describe is not proof the instance is gone:
SSH is still attempted so a network blip does not throw away scores.
"""

from __future__ import annotations

import json
import re

# EC2 ids are "i-" plus 8 hex digits (older) or 17 (current).
_INSTANCE_ID = re.compile(r"i-[0-9a-f]{8,17}\Z")
_REGION = re.compile(r"[a-z]{2}-[a-z]+-\d\Z")

# shutting-down is the terminate path. SSH is already dead, and the
# safety net's backup has finished (it runs before shutdown -h now).
# "stopped" is not in this set. Stop is not what this host is configured
# to do, and a stopped disk may still hold scores that never reached S3.
GONE_STATES = frozenset({"terminated", "shutting-down"})


def valid_instance_id(value: str) -> bool:
    return _INSTANCE_ID.fullmatch(value.strip()) is not None


def valid_region(value: str) -> bool:
    return _REGION.fullmatch(value.strip()) is not None


def describe_instances_argv(instance_id: str, region: str) -> list[str]:
    """Read-only describe of one instance. The bot policy already allows this."""
    cleaned_id = instance_id.strip()
    cleaned_region = region.strip()
    if not valid_instance_id(cleaned_id):
        raise ValueError("instance id must look like i-0123456789abcdef0")
    if not valid_region(cleaned_region):
        raise ValueError("region must look like us-east-2")
    return [
        "aws",
        "ec2",
        "describe-instances",
        "--instance-ids",
        cleaned_id,
        "--region",
        cleaned_region,
        "--output",
        "json",
        "--query",
        "Reservations[].Instances[].State.Name",
    ]


def parse_describe_instances(stdout: str, stderr: str, returncode: int) -> tuple[str | None, bool]:
    """Return ``(state, describe_failed)``.

    ``state`` is the EC2 name, such as ``running`` or ``terminated``.
    ``None`` with ``describe_failed`` false means the id is gone
    (``InvalidInstanceID.NotFound``, or an empty list). ``describe_failed``
    true means the CLI could not answer. Callers must not treat that as
    permission to skip the backup.
    """
    error_text = f"{stdout}\n{stderr}"
    if returncode != 0:
        if "InvalidInstanceID.NotFound" in error_text:
            return None, False
        return None, True
    try:
        payload = json.loads(stdout or "null")
    except json.JSONDecodeError:
        return None, True
    if isinstance(payload, list):
        if not payload:
            return None, False
        state = payload[0]
        if isinstance(state, str) and state.strip():
            return state.strip(), False
        return None, True
    return None, True


def should_skip_ssh_backup(state: str | None, *, describe_failed: bool) -> bool:
    """True only when the instance is already terminated or missing."""
    if describe_failed:
        return False
    if state is None:
        return True
    return state.strip().lower() in GONE_STATES
