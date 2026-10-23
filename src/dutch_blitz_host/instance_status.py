"""Decide whether `down` still needs an SSH backup.

A host that shut itself down is already gone by the time someone runs
`down`. The on-server timer uploaded a snapshot before it powered off.
`down` should skip SSH, then still delete the blitz DNS record and the
Terraform state. A failed describe is not proof the instance is gone:
SSH is still attempted so a network blip does not throw away scores.

A failed apply can leave ``aws_instance.game`` tainted, or in state with
no id, after ``RunInstances`` succeeded and ``ModifyInstanceAttribute``
did not. Outputs then have no public IP. That instance is not gone.
``down`` reads ``terraform show -json`` and, when the id is missing,
``DescribeInstances`` filtered to the ``Project=dutch-blitz`` tag, and
still uploads the snapshot.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# EC2 ids are "i-" plus 8 hex digits (older) or 17 (current).
_INSTANCE_ID = re.compile(r"i-[0-9a-f]{8,17}\Z")
_REGION = re.compile(r"[a-z]{2}-[a-z]+-\d\Z")
_IPV4 = re.compile(
    r"(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
    r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\Z"
)
# Name tag on the game-night instance: ``<name_prefix>-game``.
_NAME_TAG = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?\Z")

# Same tag TerminateInstances requires. Not the Name tag: Name follows
# name_prefix, Project does not.
PROJECT_TAG_KEY = "Project"
PROJECT_TAG_VALUE = "dutch-blitz"

# shutting-down is the terminate path. SSH is already dead, and the
# safety net's backup has finished (it runs before shutdown -h now).
# "stopped" is not in this set. Stop is not what this host is configured
# to do, and a stopped disk may still hold scores that never reached S3.
GONE_STATES = frozenset({"terminated", "shutting-down"})


def valid_instance_id(value: str) -> bool:
    return _INSTANCE_ID.fullmatch(value.strip()) is not None


def valid_region(value: str) -> bool:
    return _REGION.fullmatch(value.strip()) is not None


def valid_public_ipv4(value: str) -> bool:
    return _IPV4.fullmatch(value.strip()) is not None


def valid_name_tag(value: str) -> bool:
    return _NAME_TAG.fullmatch(value.strip()) is not None


@dataclass(frozen=True)
class StateGameInstance:
    """``aws_instance.game`` as ``terraform show -json`` recorded it.

    ``tainted`` is the failed-create mark. ``instance_id`` is None when
    the object is in state but the id attribute is missing or not an
    EC2 id. That is the un-id'd case.
    """

    instance_id: str | None
    public_ip: str | None
    tainted: bool


@dataclass(frozen=True)
class LiveInstance:
    """One instance from DescribeInstances."""

    instance_id: str
    state: str
    public_ip: str | None
    name: str | None = None


@dataclass(frozen=True)
class FinalBackup:
    """What ``down`` should do before ``terraform destroy``.

    ``skip`` means the host is already terminated, so SSH is omitted.
    ``host`` is the public IP to use when Terraform outputs do not have
    one. ``refuse`` means do not destroy: the instance may still hold
    scores and the backup target is not known.
    """

    skip: bool
    detail: str
    host: str | None = None
    refuse: bool = False


@dataclass(frozen=True)
class TagChoice:
    instance: LiveInstance | None
    ambiguous: bool


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


def _clean(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def parse_game_instance_show(document: Any) -> StateGameInstance | None:
    """Find ``aws_instance.game`` in ``terraform show -json``.

    None means the resource is not in state. A document that is not a
    state snapshot also returns None; the caller treats a failed
    ``terraform show`` separately from an empty state.
    """
    if not isinstance(document, dict):
        return None
    values = document.get("values")
    if not isinstance(values, dict):
        return None
    root = values.get("root_module")
    if not isinstance(root, dict):
        return None
    resources = root.get("resources")
    if not isinstance(resources, list):
        return None
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        if resource.get("type") != "aws_instance" or resource.get("name") != "game":
            continue
        if resource.get("mode") not in (None, "managed"):
            continue
        attrs = resource.get("values")
        if not isinstance(attrs, dict):
            attrs = {}
        instance_id = _clean(attrs.get("id"))
        public_ip = _clean(attrs.get("public_ip"))
        return StateGameInstance(
            instance_id=instance_id if valid_instance_id(instance_id) else None,
            public_ip=public_ip if valid_public_ipv4(public_ip) else None,
            tainted=resource.get("tainted") is True,
        )
    return None


def describe_instances_by_id_argv(instance_id: str, region: str) -> list[str]:
    """Describe one instance, including its public IP. No ``--query``."""
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
    ]


def describe_tagged_game_instances_argv(region: str) -> list[str]:
    """DescribeInstances for instances tagged ``Project=dutch-blitz``.

    That is the tag ``TerminateInstances`` already requires. The Name tag
    is not part of the filter: a create that saved a different Name would
    hide the instance, and ``down`` would skip the backup. The bot policy
    already allows ``ec2:Describe*``.
    """
    cleaned_region = region.strip()
    if not valid_region(cleaned_region):
        raise ValueError("region must look like us-east-2")
    return [
        "aws",
        "ec2",
        "describe-instances",
        "--region",
        cleaned_region,
        "--filters",
        f"Name=tag:{PROJECT_TAG_KEY},Values={PROJECT_TAG_VALUE}",
        "--output",
        "json",
    ]


def parse_described_instances(
    stdout: str, stderr: str, returncode: int
) -> tuple[list[LiveInstance], bool]:
    """Return ``(instances, describe_failed)`` from a full DescribeInstances body.

    ``InvalidInstanceID.NotFound`` is an empty list, not a failure: the
    id is gone. Any other non-zero exit is a failure. Callers must not
    treat a failure as proof that nothing is running.
    """
    error_text = f"{stdout}\n{stderr}"
    if returncode != 0:
        if "InvalidInstanceID.NotFound" in error_text:
            return [], False
        return [], True
    try:
        payload = json.loads(stdout or "null")
    except json.JSONDecodeError:
        return [], True
    if not isinstance(payload, dict):
        return [], True
    found: list[LiveInstance] = []
    reservations = payload.get("Reservations")
    if not isinstance(reservations, list):
        return [], True
    for reservation in reservations:
        if not isinstance(reservation, dict):
            continue
        instances = reservation.get("Instances")
        if not isinstance(instances, list):
            continue
        for instance in instances:
            if not isinstance(instance, dict):
                continue
            instance_id = _clean(instance.get("InstanceId"))
            if not valid_instance_id(instance_id):
                continue
            state_block = instance.get("State")
            state = ""
            if isinstance(state_block, dict):
                state = _clean(state_block.get("Name"))
            public_ip = _clean(instance.get("PublicIpAddress"))
            name = _instance_name(instance.get("Tags"))
            found.append(
                LiveInstance(
                    instance_id=instance_id,
                    state=state,
                    public_ip=public_ip if valid_public_ipv4(public_ip) else None,
                    name=name,
                )
            )
    return found, False


def _instance_name(tags: Any) -> str | None:
    if not isinstance(tags, list):
        return None
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        if _clean(tag.get("Key")) == "Name":
            value = _clean(tag.get("Value"))
            return value or None
    return None


def instance_is_gone(state: str) -> bool:
    return state.strip().lower() in GONE_STATES


def pick_tagged_instance(
    instances: list[LiveInstance], *, name_tag: str | None = None
) -> TagChoice:
    """One live match, or none when every match is already terminated.

    More than one live instance is ambiguous, unless ``name_tag`` matches
    exactly one of them. ``down`` must not guess which disk holds the scores.
    """
    live = [item for item in instances if not instance_is_gone(item.state)]
    if name_tag:
        named = [item for item in live if item.name == name_tag]
        if len(named) == 1:
            return TagChoice(named[0], False)
        if len(named) > 1:
            return TagChoice(None, True)
    if len(live) > 1:
        return TagChoice(None, True)
    if len(live) == 1:
        return TagChoice(live[0], False)
    return TagChoice(None, False)


def game_name_tag(name_prefix: str) -> str | None:
    """``<name_prefix>-game``, or None when the prefix is not a safe filter value."""
    prefix = name_prefix.strip() or "dutch-blitz"
    name = f"{prefix}-game"
    if valid_name_tag(name):
        return name
    return None
