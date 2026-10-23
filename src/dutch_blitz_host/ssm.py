"""SSM Run Command for the game-night host.

The controller has no ``ssh`` binary and no sudo. ``up``, ``configure``,
``backup``, and ``down`` use boto3: wait until
``DescribeInstanceInformation`` reports ``PingStatus=Online``,
``SendCommand`` of ``AWS-RunShellScript``, then poll
``GetCommandInvocation`` for the exit code and output.

``SendCommand`` parameters are kept in SSM command history, and so is the
command output. The boot script contains the Clerk keys, so it is never a
parameter and it is never printed. ``configure`` writes the new image pin
and the idle numbers, then re-runs the script cloud-init already stored
on the instance.
"""

from __future__ import annotations

import re
import shlex
import time
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import ClientError

from dutch_blitz_host.envfile import redact
from dutch_blitz_host.instance_status import valid_instance_id, valid_region

SSM_DOCUMENT = "AWS-RunShellScript"
CLOUD_INIT_WAIT_COMMAND = "sudo cloud-init status --wait"
BACKUP_COMMAND = "sudo /usr/local/bin/dutch-blitz-backup backup"
# Cloud-init stores a shell-script user-data part here. The raw user-data
# file is the fallback when that part path is absent.
BOOT_SCRIPT_PART = "/var/lib/cloud/instance/scripts/part-001"
BOOT_SCRIPT_USER_DATA = "/var/lib/cloud/instance/user-data.txt"
CONFIGURE_ENV = "/etc/dutch-blitz/configure.env"

# AL2023 starts the agent during boot. Registration is usually well under
# a minute. Two minutes is the wait before up gives up and says to retry.
SSM_ONLINE_TIMEOUT_SECONDS = 120
SSM_ONLINE_INTERVAL_SECONDS = 5
COMMAND_POLL_INTERVAL_SECONDS = 5
# How long the agent may take to pick the command up. The script's own
# budget is executionTimeout, passed per command.
DELIVERY_TIMEOUT_SECONDS = 120
CLOUD_INIT_WAIT_TIMEOUT_SECONDS = 1200
CONFIGURE_TIMEOUT_SECONDS = 1200
BACKUP_TIMEOUT_SECONDS = 300

# Same pin the Terraform variable accepts. latest is not a pin.
_APP_IMAGE_RE = re.compile(
    r"\Aghcr\.io/sixofone/dutch-blitz:sha-[0-9a-f]{7,40}\Z"
    r"|\Aghcr\.io/sixofone/dutch-blitz@sha256:[0-9a-f]{64}\Z"
)

_TERMINAL_STATUSES = frozenset({"Success", "Cancelled", "TimedOut", "Failed", "Cancelling"})
_ONLINE = "Online"


@dataclass(frozen=True)
class CommandResult:
    """One finished Run Command invocation."""

    status: str
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ConfigureOverrides:
    """Non-secret values ``configure`` may put in a command parameter.

    The Clerk keys are not here. They stay in the boot script that is
    already on the instance.
    """

    app_image: str
    idle_warn_minutes: int
    idle_shutdown_minutes: int
    uptime_warn_minutes: int
    uptime_shutdown_minutes: int
    idle_snooze_minutes: int
    idle_check_minutes: int


def ssm_client(region: str) -> Any:
    """Regional SSM client. Credentials come from the normal boto3 chain."""
    if not valid_region(region):
        raise SystemExit(f"Refusing SSM in unexpected region {region!r}.")
    try:
        import boto3
    except ImportError as exc:
        raise SystemExit(
            "boto3 is not installed. The controller needs it for SSM. "
            "From the repository root: pip install -r requirements-host.txt"
        ) from exc
    return boto3.client("ssm", region_name=region)


def _require_instance(instance_id: str) -> str:
    cleaned = instance_id.strip()
    if not valid_instance_id(cleaned):
        raise SystemExit(f"Refusing SSM to unexpected instance {instance_id!r}.")
    return cleaned


def wait_for_ssm_online(
    instance_id: str,
    region: str,
    *,
    timeout: float = SSM_ONLINE_TIMEOUT_SECONDS,
    interval: float = SSM_ONLINE_INTERVAL_SECONDS,
    client: Any | None = None,
    sleep: Any = time.sleep,
    clock: Any = time.monotonic,
) -> bool:
    """Poll until the agent reports ``PingStatus=Online``, or the wait ends.

    A new instance is not a managed instance the moment Terraform returns.
    The agent that ships with Amazon Linux 2023 registers itself. One to
    two minutes is the budget.
    """
    target = _require_instance(instance_id)
    ssm = client if client is not None else ssm_client(region)
    deadline = clock() + timeout
    attempt = 0
    while True:
        attempt += 1
        if _ping_status(ssm, target) == _ONLINE:
            print(f"SSM agent is online on {target} (attempt {attempt}).")
            return True
        if clock() + interval > deadline:
            return False
        print(f"Waiting for the SSM agent on {target} (attempt {attempt})...")
        sleep(interval)


def describe_ping_status(
    instance_id: str,
    region: str,
    *,
    client: Any | None = None,
) -> str | None:
    """Return ``PingStatus`` for one instance, or None when it is absent.

    ``status`` uses this. It does not send a command and it does not
    print command history.
    """
    target = _require_instance(instance_id)
    ssm = client if client is not None else ssm_client(region)
    return _ping_status(ssm, target)


def _ping_status(client: Any, instance_id: str) -> str | None:
    response = client.describe_instance_information(
        InstanceInformationFilterList=[
            {"key": "InstanceIds", "valueSet": [instance_id]},
        ],
        MaxResults=5,
    )
    infos = response.get("InstanceInformationList") or []
    for item in infos:
        if not isinstance(item, dict):
            continue
        if item.get("InstanceId") != instance_id:
            continue
        status = item.get("PingStatus")
        if isinstance(status, str):
            return status
    return None


def configure_shell(overrides: ConfigureOverrides) -> str:
    """Shell that re-runs the on-instance boot script.

    The image pin and the idle numbers are the only values interpolated.
    Both are checked here. A value that does not match is refused before
    it can become a ``SendCommand`` parameter.
    """
    image = overrides.app_image.strip()
    if _APP_IMAGE_RE.fullmatch(image) is None:
        raise ValueError(
            "app_image must be ghcr.io/sixofone/dutch-blitz:sha-<commit> "
            "or ghcr.io/sixofone/dutch-blitz@sha256:<64 hex digits>."
        )
    _minutes("idle_warn_minutes", overrides.idle_warn_minutes, 1, 10080)
    _minutes("idle_shutdown_minutes", overrides.idle_shutdown_minutes, 2, 10080)
    _minutes("uptime_warn_minutes", overrides.uptime_warn_minutes, 1, 10080)
    _minutes("uptime_shutdown_minutes", overrides.uptime_shutdown_minutes, 2, 10080)
    _minutes("idle_snooze_minutes", overrides.idle_snooze_minutes, 1, 1440)
    _minutes("idle_check_minutes", overrides.idle_check_minutes, 1, 60)
    if overrides.idle_warn_minutes >= overrides.idle_shutdown_minutes:
        raise ValueError("idle_warn_minutes must be less than idle_shutdown_minutes.")
    if overrides.uptime_warn_minutes >= overrides.uptime_shutdown_minutes:
        raise ValueError("uptime_warn_minutes must be less than uptime_shutdown_minutes.")
    pairs = (
        ("APP_IMAGE", image),
        ("IDLE_WARN_MINUTES", str(overrides.idle_warn_minutes)),
        ("IDLE_SHUTDOWN_MINUTES", str(overrides.idle_shutdown_minutes)),
        ("UPTIME_WARN_MINUTES", str(overrides.uptime_warn_minutes)),
        ("UPTIME_SHUTDOWN_MINUTES", str(overrides.uptime_shutdown_minutes)),
        ("IDLE_SNOOZE_MINUTES", str(overrides.idle_snooze_minutes)),
        ("IDLE_CHECK_MINUTES", str(overrides.idle_check_minutes)),
    )
    lines = [
        "set -eu",
        "install -d -m 0755 /etc/dutch-blitz",
        f"rm -f {CONFIGURE_ENV}",
        "umask 022",
    ]
    for key, value in pairs:
        lines.append("printf '%s\\n' " + shlex.quote(f"{key}={value}") + f" >> {CONFIGURE_ENV}")
    lines.extend(
        [
            f"chmod 0644 {CONFIGURE_ENV}",
            f"script={BOOT_SCRIPT_PART}",
            f'if [ ! -f "$script" ]; then script={BOOT_SCRIPT_USER_DATA}; fi',
            'if [ ! -f "$script" ]; then echo "boot script is not on the instance" >&2; exit 1; fi',
            'sudo bash "$script"',
        ]
    )
    return "\n".join(lines) + "\n"


def _minutes(name: str, value: int, low: int, high: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a whole number of minutes.")
    if value < low or value > high:
        raise ValueError(f"{name} must be from {low} to {high}.")


def run_shell_script(
    instance_id: str,
    region: str,
    script: str,
    *,
    timeout_seconds: int,
    comment: str,
    client: Any | None = None,
    secrets: list[str] | None = None,
    sleep: Any = time.sleep,
    clock: Any = time.monotonic,
) -> CommandResult:
    """Send one shell script and poll until SSM reports a terminal status.

    ``script`` is the command parameter. Callers must not put secrets in
    it. ``secrets``, when the controller knows them, are redacted from
    the output this function prints. Output is also stored by SSM, so the
    remote script must not print the Clerk keys either.
    """
    if not script.strip():
        raise SystemExit("Refusing to send an empty SSM command.")
    hidden = [item for item in (secrets or []) if item]
    for secret in hidden:
        if secret in script:
            raise SystemExit("Refusing to send an SSM command that contains a secret.")
    target = _require_instance(instance_id)
    if timeout_seconds < 30:
        raise SystemExit("SSM execution timeout must be at least 30 seconds.")
    ssm = client if client is not None else ssm_client(region)
    print(f"SSM SendCommand {target} {SSM_DOCUMENT}: {comment}")
    response = ssm.send_command(
        InstanceIds=[target],
        DocumentName=SSM_DOCUMENT,
        TimeoutSeconds=DELIVERY_TIMEOUT_SECONDS,
        Comment=comment[:100],
        Parameters={
            "commands": [script],
            "executionTimeout": [str(timeout_seconds)],
        },
    )
    command = response.get("Command") or {}
    command_id = command.get("CommandId")
    if not isinstance(command_id, str) or not command_id.strip():
        raise SystemExit("SSM SendCommand did not return a command id.")
    # The script may run for timeout_seconds. Polling stops a little
    # after that so a final GetCommandInvocation can still land.
    deadline = clock() + timeout_seconds + DELIVERY_TIMEOUT_SECONDS
    invocation: dict[str, Any] | None = None
    while True:
        invocation = _poll_invocation(ssm, command_id.strip(), target)
        if invocation is not None and invocation.get("Status") in _TERMINAL_STATUSES:
            break
        if clock() + COMMAND_POLL_INTERVAL_SECONDS > deadline:
            break
        sleep(COMMAND_POLL_INTERVAL_SECONDS)
    result = _result_from_invocation(invocation)
    _print_streams(result, hidden)
    if result.status == "Success":
        print(f"SSM command {command_id} on {target} exited {result.exit_code}.")
    else:
        print(
            f"SSM command {command_id} on {target} finished "
            f"{result.status} (exit {result.exit_code})."
        )
    return result


def _poll_invocation(client: Any, command_id: str, instance_id: str) -> dict[str, Any] | None:
    """One GetCommandInvocation. Not-yet-visible is None, not a failure."""
    try:
        response = client.get_command_invocation(
            CommandId=command_id,
            InstanceId=instance_id,
        )
    except ClientError as exc:
        code = ""
        error = exc.response.get("Error") if isinstance(exc.response, dict) else None
        if isinstance(error, dict) and isinstance(error.get("Code"), str):
            code = error["Code"]
        if code == "InvocationDoesNotExist":
            return None
        raise
    if isinstance(response, dict):
        return response
    return None


def _result_from_invocation(invocation: dict[str, Any] | None) -> CommandResult:
    if invocation is None:
        return CommandResult("TimedOut", 1, "", "")
    status = invocation.get("Status")
    if not isinstance(status, str) or not status:
        status = "Failed"
    code = invocation.get("ResponseCode")
    if isinstance(code, int) and not isinstance(code, bool) and code >= 0:
        exit_code = code
    elif status == "Success":
        exit_code = 0
    else:
        exit_code = 1
    stdout = invocation.get("StandardOutputContent")
    stderr = invocation.get("StandardErrorContent")
    return CommandResult(
        status,
        exit_code,
        stdout if isinstance(stdout, str) else "",
        stderr if isinstance(stderr, str) else "",
    )


def _print_streams(result: CommandResult, secrets: list[str]) -> None:
    stdout = redact(result.stdout, secrets).strip()
    stderr = redact(result.stderr, secrets).strip()
    if stdout:
        print(stdout)
    if stderr:
        print(stderr)
