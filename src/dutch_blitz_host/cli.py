"""Game-night commands: apply, configure, backup, and destroy.

Local-first: with ``enable_aws`` left false, apply only records that AWS is
off. A night sets ``enable_aws = true`` in the gitignored tfvars file.

The instance boots from cloud-init. ``up`` waits for the SSM agent, then
for that script. ``configure`` applies the game-night stack and re-runs the
boot script already stored on the instance. A new image pin is written
beside that script. The script itself is not a SendCommand parameter: it
contains the Clerk keys, and SSM keeps command parameters.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from dutch_blitz_host.backup import BACKUP_STACK, NIGHT_STACK, destroy_plan
from dutch_blitz_host.dns import wait_for_a_record
from dutch_blitz_host.envfile import assert_deployable, merged_env, redact
from dutch_blitz_host.hostname import dns_fqdn, https_origin
from dutch_blitz_host.instance_status import (
    PROJECT_TAG_VALUE,
    FinalBackup,
    LiveInstance,
    StateGameInstance,
    describe_instances_argv,
    describe_tagged_game_instances_argv,
    game_name_tag,
    parse_describe_instances,
    parse_described_instances,
    parse_game_instance_show,
    pick_tagged_instance,
    should_skip_backup,
    valid_instance_id,
    valid_region,
)
from dutch_blitz_host.ssm import (
    BACKUP_COMMAND,
    BACKUP_TIMEOUT_SECONDS,
    CLOUD_INIT_WAIT_COMMAND,
    CLOUD_INIT_WAIT_TIMEOUT_SECONDS,
    CONFIGURE_TIMEOUT_SECONDS,
    SSM_ONLINE_TIMEOUT_SECONDS,
    CommandResult,
    ConfigureOverrides,
    configure_shell,
    run_shell_script,
    wait_for_ssm_online,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
NIGHT_DIR = REPO_ROOT / NIGHT_STACK
BACKUP_DIR = REPO_ROOT / BACKUP_STACK
TFVARS = NIGHT_DIR / "terraform.tfvars"
# Shared partial backend. Gitignored. The example file next to it is tracked.
BACKEND_CONFIG = NIGHT_DIR / "backend.hcl"
# A second apply (the Mac and the Linux box) waits this long for the
# S3 lock file instead of failing at once. The default Terraform wait is 0s.
LOCK_TIMEOUT = "120s"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Host a Dutch Blitz game night on EC2")
    subparsers = parser.add_subparsers(dest="command", required=True)

    up = subparsers.add_parser(
        "up", help="Create the bucket if needed, start the instance, configure it, print the URL"
    )
    _add_init(up)

    apply = subparsers.add_parser(
        "apply", help="Terraform apply the game-night instance (local no-op until enable_aws=true)"
    )
    apply.add_argument("--dry-run", action="store_true", help="terraform plan instead of apply")
    _add_init(apply)

    backup_apply = subparsers.add_parser(
        "backup-apply",
        help="Terraform apply the durable S3 backup bucket (not destroyed by down)",
    )
    backup_apply.add_argument(
        "--dry-run", action="store_true", help="terraform plan instead of apply"
    )
    _add_init(backup_apply)

    configure = subparsers.add_parser(
        "configure",
        help="Re-run the boot script already on the instance (retry S3, or apply a new image pin)",
    )
    configure.add_argument("--dry-run", action="store_true", help="print the SSM command only")

    backup = subparsers.add_parser("backup", help="Upload a consistent SQLite snapshot to S3")
    backup.add_argument("--dry-run", action="store_true", help="print the SSM command only")

    destroy = subparsers.add_parser(
        "destroy",
        help="Back up the score book, then terraform destroy the instance. The S3 bucket stays.",
    )
    destroy.add_argument("--dry-run", action="store_true", help="print the destroy plan only")
    destroy.add_argument(
        "--skip-backup",
        action="store_true",
        help="Destroy even if the instance cannot be reached. Scores since the last upload are lost.",
    )
    _add_init(destroy)

    down = subparsers.add_parser("down", help="Same as destroy")
    down.add_argument("--dry-run", action="store_true")
    down.add_argument("--skip-backup", action="store_true")
    _add_init(down)
    return parser


def _add_init(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--init",
        action="store_true",
        help="terraform init before plan, apply, or destroy. AWS nights init on every command either way, so the S3 backend config is applied.",
    )


def parse_tfvars(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        values[key.strip()] = raw.strip().strip('"').strip("'")
    return values


def aws_enabled(tfvars: dict[str, str]) -> bool:
    return tfvars.get("enable_aws", "false").strip().lower() == "true"


def load_tfvars(path: Path = TFVARS) -> dict[str, str]:
    if not path.is_file():
        return {}
    return parse_tfvars(path.read_text(encoding="utf-8"))


def load_controller_env(repo_root: Path = REPO_ROOT) -> dict[str, str]:
    dotenv_path = repo_root / ".env"
    dotenv_text = dotenv_path.read_text(encoding="utf-8") if dotenv_path.is_file() else None
    return merged_env(os.environ, dotenv_text)


def clerk_terraform_env(env: dict[str, str]) -> dict[str, str]:
    """Terraform variables for the boot script. Not command-line arguments.

    ``TF_VAR_`` is read by Terraform and is not printed by this program.
    The keys are sensitive variables, so a plan redacts the rendered script.
    """
    return {
        "TF_VAR_clerk_publishable_key": env["CLERK_PUBLISHABLE_KEY"].strip(),
        "TF_VAR_clerk_secret_key": env["CLERK_SECRET_KEY"].strip(),
    }


def _fmt(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def _terraform() -> str:
    terraform = shutil.which("terraform")
    if not terraform:
        raise SystemExit(
            "terraform not found on PATH. Install Terraform 1.10 or newer "
            "(https://developer.hashicorp.com/terraform/install). "
            "1.10 is the first release that locks S3 state with use_lockfile. CI uses 1.16.5."
        )
    return terraform


def require_backend_config(path: Path = BACKEND_CONFIG) -> Path:
    """Return the gitignored backend file, or stop with a copy instruction.

    The bucket name cannot live in a committed backend block. Terraform
    reads that block before variables exist, so ``up`` and ``down`` pass
    this file as ``-backend-config``.
    """
    if not path.is_file():
        raise SystemExit(
            "Remote state is not configured. Copy terraform/backend.hcl.example "
            "to terraform/backend.hcl and set the state bucket name. "
            "That file is gitignored. The one-time migration is docs/remote-state.md."
        )
    return path


def terraform_init_argv(terraform: str, backend_config: Path) -> list[str]:
    """Init the S3 backend without prompting.

    ``-backend-config`` fills in the bucket and region. ``-input=false``
    makes a missing value fail instead of waiting for a person, which
    matters when the assistant runs this. This does not pass
    ``-migrate-state``: copying local state into S3 is a one-time step
    the owner runs by hand.
    """
    return [
        terraform,
        "init",
        "-input=false",
        f"-backend-config={backend_config}",
    ]


def terraform_local_init_argv(terraform: str) -> list[str]:
    """Init without contacting S3.

    ``enable_aws`` false is the local no-op. It has no state bucket and
    no credentials. CI uses the same ``-backend=false`` flag.
    """
    return [terraform, "init", "-backend=false", "-input=false"]


def _run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
    printable = redact(_fmt(cmd), _secret_values(env or {}))
    print("Launch:", printable)
    completed = subprocess.run(cmd, cwd=str(cwd), env=env, check=False)
    return completed.returncode


def _secret_values(env: dict[str, str]) -> list[str]:
    values = []
    for key in ("CLERK_SECRET_KEY", "CLERK_PUBLISHABLE_KEY"):
        value = env.get(key, "").strip()
        if value:
            values.append(value)
    return values


def terraform_outputs(tf_dir: Path) -> dict[str, Any]:
    raw = subprocess.check_output(
        [_terraform(), "output", "-json"],
        cwd=str(tf_dir),
        text=True,
        stderr=subprocess.PIPE,
    )
    return cast(dict[str, Any], json.loads(raw))


def output_value(outputs: dict, name: str) -> str | None:
    value = (outputs.get(name) or {}).get("value")
    if value is None or value == "":
        return None
    return str(value)


def require_aws_outputs(outputs: dict) -> tuple[str, str, str, str]:
    mode = output_value(outputs, "mode")
    ip = output_value(outputs, "public_ip")
    hostname = output_value(outputs, "hostname")
    bucket = output_value(outputs, "backup_bucket")
    instance_id = output_value(outputs, "instance_id")
    if mode != "aws" or not ip or not hostname or not bucket or not instance_id:
        raise SystemExit(
            "AWS host unavailable. Set enable_aws=true in terraform/terraform.tfvars "
            "and run `PYTHONPATH=src python3 -m dutch_blitz_host up`."
        )
    if not valid_instance_id(instance_id):
        raise SystemExit(f"Terraform instance id {instance_id!r} is not an EC2 id.")
    return ip, hostname, bucket, instance_id


def _init_terraform(tf_dir: Path, *, var_file: Path | None, do_init: bool) -> int:
    terraform = _terraform()
    values = load_tfvars(var_file) if var_file is not None and var_file.is_file() else {}
    # An AWS night always inits. A previous local init would otherwise
    # keep using the Mac's terraform.tfstate after this repo grew a
    # backend block. The local no-op still skips S3.
    if aws_enabled(values):
        return _run(terraform_init_argv(terraform, require_backend_config()), tf_dir)
    if do_init or not (tf_dir / ".terraform").exists():
        return _run(terraform_local_init_argv(terraform), tf_dir)
    return 0


def cmd_terraform(
    tf_dir: Path,
    *,
    action: str,
    do_init: bool,
    var_file: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> int:
    if not (tf_dir / "main.tf").is_file():
        raise SystemExit(f"Terraform files missing under {tf_dir}")
    code = _init_terraform(tf_dir, var_file=var_file, do_init=do_init)
    if code != 0:
        return code
    cmd = [_terraform(), action, "-input=false", f"-lock-timeout={LOCK_TIMEOUT}"]
    if var_file is not None and var_file.is_file():
        cmd.extend(["-var-file", str(var_file)])
    if action == "apply":
        cmd.append("-auto-approve")
    if action == "destroy":
        cmd.append("-auto-approve")
    child = os.environ.copy()
    if extra_env:
        child.update(extra_env)
    return _run(cmd, tf_dir, env=child)


def night_terraform_env() -> dict[str, str] | None:
    """Clerk keys for an AWS night. A local no-op does not need them."""
    if not aws_enabled(load_tfvars()):
        return None
    env = load_controller_env()
    assert_deployable(env)
    return clerk_terraform_env(env)


def _minutes_from_tfvars(tfvars: dict[str, str], name: str, default: int) -> int:
    raw = tfvars.get(name, "").strip()
    if not raw:
        return default
    if not raw.isdigit():
        raise SystemExit(f"{name} in terraform.tfvars must be a whole number of minutes.")
    return int(raw)


def configure_overrides_from(tfvars: dict[str, str], app_image: str) -> ConfigureOverrides:
    """Image pin and idle numbers from outputs and tfvars. No Clerk keys."""
    try:
        return ConfigureOverrides(
            app_image=app_image,
            idle_warn_minutes=_minutes_from_tfvars(tfvars, "idle_warn_minutes", 60),
            idle_shutdown_minutes=_minutes_from_tfvars(tfvars, "idle_shutdown_minutes", 90),
            uptime_warn_minutes=_minutes_from_tfvars(tfvars, "uptime_warn_minutes", 420),
            uptime_shutdown_minutes=_minutes_from_tfvars(tfvars, "uptime_shutdown_minutes", 480),
            idle_snooze_minutes=_minutes_from_tfvars(tfvars, "idle_snooze_minutes", 120),
            idle_check_minutes=_minutes_from_tfvars(tfvars, "idle_check_minutes", 15),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _command_exit(result: CommandResult) -> int:
    if result.status == "Success" and result.exit_code == 0:
        return 0
    if result.exit_code > 0:
        return result.exit_code
    return 1


def cmd_backup(
    *,
    dry_run: bool,
    instance_id: str | None = None,
    region: str | None = None,
) -> int:
    """Upload a snapshot over SSM.

    ``instance_id`` is set when Terraform outputs have none, which is what
    a tainted create leaves behind. The id has to look like an EC2 id.
    SSM does not use the public IP. The agent calls out to the service.
    """
    tfvars = load_tfvars()
    if instance_id is None:
        outputs = terraform_outputs(NIGHT_DIR)
        _ip, _hostname, _bucket, instance_id = require_aws_outputs(outputs)
    if region is None:
        region = _night_region(tfvars)
    if not valid_instance_id(instance_id):
        raise SystemExit(f"Refusing SSM backup to unexpected instance {instance_id!r}.")
    if not valid_region(region):
        raise SystemExit(f"Refusing SSM backup in unexpected region {region!r}.")
    print(f"Backup: SSM {instance_id} {BACKUP_COMMAND}")
    if dry_run:
        print("Dry run only; no backup was uploaded.")
        return 0
    result = run_shell_script(
        instance_id,
        region,
        BACKUP_COMMAND,
        timeout_seconds=BACKUP_TIMEOUT_SECONDS,
        comment="dutch-blitz backup",
        secrets=_secret_values(load_controller_env()),
    )
    return _command_exit(result)


def wait_for_ssm(instance_id: str, region: str, *, client: Any | None = None) -> bool:
    """Wait until the agent is Online. The budget is two minutes."""
    if wait_for_ssm_online(instance_id, region, client=client):
        return True
    print(
        f"The SSM agent on {instance_id} was not Online within "
        f"{SSM_ONLINE_TIMEOUT_SECONDS} seconds. "
        "Amazon Linux 2023 starts it at boot. When DescribeInstanceInformation "
        "shows PingStatus=Online, run: "
        "PYTHONPATH=src python3 -m dutch_blitz_host configure",
        file=sys.stderr,
    )
    return False


def wait_for_cloud_init(
    instance_id: str,
    region: str,
    *,
    require_success: bool,
    client: Any | None = None,
) -> bool:
    """Block until cloud-init finishes. First boot is what runs the script.

    ``require_success`` is for ``up``. ``configure`` only needs the first
    boot to be finished so a second run does not overlap it. A finished
    error still returns true in that case. A timeout does not: the script
    may still be running.
    """
    result = run_shell_script(
        instance_id,
        region,
        CLOUD_INIT_WAIT_COMMAND,
        timeout_seconds=CLOUD_INIT_WAIT_TIMEOUT_SECONDS,
        comment="dutch-blitz cloud-init status --wait",
        client=client,
        secrets=_secret_values(load_controller_env()),
    )
    if result.status == "Success" and result.exit_code == 0:
        print(f"cloud-init finished on {instance_id}.")
        return True
    finished_with_error = result.status == "Failed" and result.exit_code > 0
    if finished_with_error and not require_success:
        print(f"cloud-init on {instance_id} finished with an error. Running the boot script again.")
        return True
    if not finished_with_error:
        print(
            f"cloud-init on {instance_id} did not finish within "
            f"{CLOUD_INIT_WAIT_TIMEOUT_SECONDS} seconds. "
            "Read /var/log/cloud-init-output.log from Session Manager, then run configure.",
            file=sys.stderr,
        )
        return False
    print(
        f"cloud-init on {instance_id} failed. The boot script's own log is "
        "/var/log/cloud-init-output.log on the instance. "
        "When the instance role can reach S3, run: "
        "PYTHONPATH=src python3 -m dutch_blitz_host configure",
        file=sys.stderr,
    )
    return False


def _configure_script(tfvars: dict[str, str], app_image: str) -> str:
    try:
        return configure_shell(configure_overrides_from(tfvars, app_image))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def cmd_configure(*, dry_run: bool) -> int:
    """Apply the night stack, then re-run the boot script already on the host.

    Apply does not replace the instance. user_data changes are ignored so
    a new image pin or idle limit does not stop the machine. The script
    cloud-init stored on first boot is what the host runs. ``configure``
    writes the current image pin and idle numbers to a file that script
    reads. Those values are not secrets. The Clerk keys stay in the
    on-instance script and are not SendCommand parameters.
    """
    env = load_controller_env()
    assert_deployable(env)
    tfvars = load_tfvars()
    outputs = terraform_outputs(NIGHT_DIR)
    ip, hostname, _bucket, instance_id = require_aws_outputs(outputs)
    region = _night_region(tfvars)
    app_image = output_value(outputs, "app_image") or ""
    script = _configure_script(tfvars, app_image)
    print(f"Hostname: {hostname}")
    print(f"Share URL: {https_origin(hostname)}")
    print(f"SSM {instance_id}: re-run the boot script already on the instance.")
    print("The boot script is not sent. It contains the Clerk keys.")
    if dry_run:
        print(script, end="")
        print("Dry run only; the host was not changed.")
        return 0
    if not _dns_ready(tfvars, hostname=hostname, public_ip=ip, dry_run=False):
        return 1
    code = cmd_terraform(
        NIGHT_DIR,
        action="apply",
        do_init=False,
        var_file=TFVARS,
        extra_env=clerk_terraform_env(env),
    )
    if code != 0:
        return code
    outputs = terraform_outputs(NIGHT_DIR)
    _ip, _hostname, _bucket, instance_id = require_aws_outputs(outputs)
    app_image = output_value(outputs, "app_image") or ""
    script = _configure_script(tfvars, app_image)
    if not wait_for_ssm(instance_id, region):
        return 1
    if not wait_for_cloud_init(instance_id, region, require_success=False):
        return 1
    result = run_shell_script(
        instance_id,
        region,
        script,
        timeout_seconds=CONFIGURE_TIMEOUT_SECONDS,
        comment="dutch-blitz configure",
        secrets=_secret_values(env),
    )
    return _command_exit(result)


def _dns_ready(tfvars: dict[str, str], *, hostname: str, public_ip: str, dry_run: bool) -> bool:
    """Wait for the Route 53 name before Caddy asks Let's Encrypt for a certificate.

    Unset DNS variables skip this. sslip.io does not need a lookup.
    """
    zone = tfvars.get("dns_zone_name", "")
    if not zone.strip():
        return True
    record_name = tfvars.get("dns_record_name")
    try:
        fqdn = dns_fqdn(zone, record_name)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not fqdn:
        return True
    if hostname != fqdn:
        raise SystemExit(
            f"Terraform hostname is {hostname}, expected {fqdn}. "
            "Re-apply the game-night stack so the Route 53 name is the public hostname."
        )
    if dry_run:
        print(f"Dry run: would wait for {fqdn} to resolve to {public_ip}.")
        return True
    print(f"Waiting for {fqdn} to resolve to {public_ip} before requesting a certificate.")
    try:
        ready = wait_for_a_record(fqdn, public_ip, zone=fqdn.split(".", 1)[1])
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if ready:
        return True
    print(
        f"{fqdn} did not resolve to {public_ip} on the authoritative nameservers "
        "and the public resolvers before the timeout. "
        "When the name resolves, run: PYTHONPATH=src python3 -m dutch_blitz_host configure",
        file=sys.stderr,
    )
    return False


def cmd_up(*, do_init: bool) -> int:
    tfvars = load_tfvars()
    if not aws_enabled(tfvars):
        raise SystemExit(
            "enable_aws is false, so no instance will be created. "
            "Copy terraform/terraform.tfvars.example to terraform/terraform.tfvars, "
            "set enable_aws = true and a backup bucket name, then re-run."
        )
    controller = load_controller_env()
    assert_deployable(controller)
    code = cmd_terraform(BACKUP_DIR, action="apply", do_init=do_init, var_file=TFVARS)
    if code != 0:
        return code
    # The instance profile is not created here. terraform/game-role is
    # owner-only. This apply looks the profile up by name and passes it
    # to RunInstances. It fails if that root has not been applied.
    # A host that already terminated itself is still in this state until
    # the refresh below. Refresh sees that the instance is gone and
    # creates a new one. The blitz A record moves to the new public IP.
    # user_data is the cloud-init script. The instance runs it on first boot.
    code = cmd_terraform(
        NIGHT_DIR,
        action="apply",
        do_init=do_init,
        var_file=TFVARS,
        extra_env=clerk_terraform_env(controller),
    )
    if code != 0:
        return code
    outputs = terraform_outputs(NIGHT_DIR)
    ip, hostname, _bucket, instance_id = require_aws_outputs(outputs)
    region = _night_region(tfvars)
    if not wait_for_ssm(instance_id, region):
        return 1
    if not _dns_ready(tfvars, hostname=hostname, public_ip=ip, dry_run=False):
        return 1
    if not wait_for_cloud_init(instance_id, region, require_success=True):
        return 1
    print(f"Game night is up. Share {https_origin(hostname)}")
    print(f"Image: {output_value(outputs, 'app_image')}")
    print("When the night is over: PYTHONPATH=src python3 -m dutch_blitz_host down")
    return 0


def describe_instance_state(
    instance_id: str, region: str, *, run: Any = subprocess.run
) -> tuple[str | None, bool]:
    """Read one instance's state. A missing AWS CLI is "could not tell"."""
    if shutil.which("aws") is None:
        return None, True
    completed = run(
        describe_instances_argv(instance_id, region),
        text=True,
        capture_output=True,
        check=False,
    )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    return parse_describe_instances(stdout, stderr, completed.returncode)


def game_instance_from_state(tf_dir: Path = NIGHT_DIR) -> tuple[StateGameInstance | None, bool]:
    """Read ``aws_instance.game`` from ``terraform show -json``.

    Returns ``(instance, read_failed)``. ``(None, False)`` means state
    was read and the resource is absent. The JSON is not printed: user
    data in that document contains the Clerk keys.
    """
    terraform = shutil.which("terraform")
    if not terraform:
        return None, True
    try:
        raw = subprocess.check_output(
            [terraform, "show", "-json"],
            cwd=str(tf_dir),
            text=True,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError:
        return None, True
    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        return None, True
    return parse_game_instance_show(document), False


def find_instances_by_tag(
    region: str, *, run: Any = subprocess.run
) -> tuple[list[LiveInstance], bool]:
    """DescribeInstances for ``Project=dutch-blitz``.

    Returns ``(instances, describe_failed)``. A missing AWS CLI is a
    failure, not an empty night. The Name tag is applied later, and only
    to choose among more than one running instance.
    """
    if shutil.which("aws") is None:
        return [], True
    completed = run(
        describe_tagged_game_instances_argv(region),
        text=True,
        capture_output=True,
        check=False,
    )
    return parse_described_instances(
        completed.stdout or "",
        completed.stderr or "",
        completed.returncode,
    )


def _night_region(tfvars: dict[str, str]) -> str:
    return tfvars.get("aws_region", "").strip() or "us-east-2"


def _describe_known_instance(
    instance_id: str,
    region: str,
    *,
    prefix: str,
) -> FinalBackup:
    """Describe one id and decide whether SSM still has somewhere to go.

    ``prefix`` is empty for a normal id, or ``"tainted "`` when state
    marked the object tainted. The id is enough. SSM does not need a
    public address. A missing address used to block the SSH backup.
    """
    if not valid_region(region):
        return FinalBackup(
            False,
            "aws_region is not a region name; trying the SSM backup",
            instance_id,
        )
    state, failed = describe_instance_state(instance_id, region)
    if should_skip_backup(state, describe_failed=failed):
        label = state or "missing"
        return FinalBackup(True, f"{instance_id} is {label}")
    if failed:
        return FinalBackup(
            False,
            "could not describe the instance; trying the SSM backup",
            instance_id,
        )
    noun = f"{prefix}instance"
    return FinalBackup(False, f"{noun} is {state}", instance_id)


def _backup_without_output_id(tfvars: dict[str, str]) -> FinalBackup:
    """Outputs have no instance id. State or the Project tag may still name one.

    An empty state really is an empty night: skip the backup and destroy.
    A tainted or un-id'd ``aws_instance.game`` is not empty. Find the
    instance and back it up over SSM. A describe failure is not proof it
    is gone.
    """
    state_instance, state_failed = game_instance_from_state()
    region = _night_region(tfvars)
    if state_instance is not None and state_instance.instance_id:
        prefix = "tainted " if state_instance.tainted else ""
        return _describe_known_instance(
            state_instance.instance_id,
            region,
            prefix=prefix,
        )

    if state_instance is None and not state_failed:
        return FinalBackup(True, "Terraform state has no instance id")

    if state_instance is not None and state_instance.tainted:
        why = "tainted instance has no id"
    elif state_instance is not None:
        why = "instance in state has no id"
    else:
        why = "Terraform state could not be read"
    if not valid_region(region):
        return FinalBackup(
            False,
            f"{why}; aws_region is not a region name. "
            "The final backup was not uploaded, and the instance was left up.",
            refuse=True,
        )
    name_tag = game_name_tag(tfvars.get("name_prefix", ""))
    tagged, tag_failed = find_instances_by_tag(region)
    if tag_failed:
        return FinalBackup(
            False,
            f"{why}; could not describe instances by tag. "
            "The final backup was not uploaded, and the instance was left up.",
            refuse=True,
        )
    choice = pick_tagged_instance(tagged, name_tag=name_tag)
    if choice.ambiguous:
        return FinalBackup(
            False,
            f"{why}. More than one running instance is tagged "
            f"Project={PROJECT_TAG_VALUE}. "
            "The final backup was not uploaded, and the instance was left up.",
            refuse=True,
        )
    if choice.instance is None:
        if state_failed:
            return FinalBackup(
                False,
                "Terraform state could not be read, and no running instance is tagged "
                f"Project={PROJECT_TAG_VALUE}. "
                "The final backup was not uploaded, and the instance was left up.",
                refuse=True,
            )
        return FinalBackup(
            True,
            f"{why}, and no running instance is tagged Project={PROJECT_TAG_VALUE}",
        )
    return FinalBackup(
        False,
        f"{why}; {choice.instance.instance_id} is {choice.instance.state}, "
        f"found by tag Project={PROJECT_TAG_VALUE}",
        choice.instance.instance_id,
    )


def instance_already_gone(tfvars: dict[str, str]) -> FinalBackup:
    """Whether ``down`` should skip the backup because the host already terminated.

    ``skip`` is false when describe fails or the id looks wrong. Those
    cases still try SSM, so a blip does not destroy a night whose latest
    scores never reached S3.

    No id in the outputs is not, by itself, proof the instance is gone.
    A tainted or un-id'd ``aws_instance.game`` is looked up in state and
    then by tag before the backup is skipped.
    """
    try:
        outputs = terraform_outputs(NIGHT_DIR)
    except subprocess.CalledProcessError:
        outputs = {}
    instance_id = output_value(outputs, "instance_id")
    if instance_id and not valid_instance_id(instance_id):
        return FinalBackup(
            False,
            f"instance id {instance_id!r} is unexpected. "
            "The final backup was not uploaded, and the instance was left up.",
            refuse=True,
        )
    if not instance_id:
        return _backup_without_output_id(tfvars)
    return _describe_known_instance(instance_id, _night_region(tfvars), prefix="")


def cmd_destroy(*, dry_run: bool, skip_backup: bool, do_init: bool) -> int:
    print(f"The backup bucket stack ({BACKUP_STACK}) is not destroyed.")
    print("The instance role and the idle SNS topic (terraform/game-role) are not destroyed.")
    if dry_run:
        steps = destroy_plan(skip_backup=skip_backup)
        print("Destroy plan:", ", ".join(steps))
        print(
            "If the instance already terminated itself, down skips the backup "
            "and still removes the blitz DNS record and Terraform state."
        )
        print(
            "If state has a tainted or un-id'd instance, down looks it up by the "
            "Project=dutch-blitz tag and still uploads the final snapshot over SSM."
        )
        print("Dry run only; nothing was destroyed.")
        return 0
    tfvars = load_tfvars()
    if not aws_enabled(tfvars):
        print("enable_aws is false. There is no game-night instance to destroy.")
        return 0
    gone = False
    if not skip_backup:
        plan = instance_already_gone(tfvars)
        if plan.refuse:
            print(plan.detail, file=sys.stderr)
            return 1
        gone = plan.skip
        if gone:
            print(
                f"The instance is already gone ({plan.detail}). Skipping the backup. "
                "The on-server shutdown uploads a snapshot before it powers off. "
                "DNS and Terraform state are still removed."
            )
        else:
            print(f"{plan.detail}. Uploading a final snapshot.")
            code = cmd_backup(
                dry_run=False,
                instance_id=plan.instance_id,
                region=_night_region(tfvars),
            )
            if code != 0:
                print(
                    "Final backup failed, so the instance was left up. "
                    "Fix SSM or S3, or re-run with --skip-backup if the host is already gone.",
                    file=sys.stderr,
                )
                return code
    steps = destroy_plan(skip_backup=skip_backup, instance_gone=gone)
    print("Destroy plan:", ", ".join(steps))
    return cmd_terraform(NIGHT_DIR, action="destroy", do_init=do_init, var_file=TFVARS)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "up":
        return cmd_up(do_init=args.init)
    if args.command == "apply":
        return cmd_terraform(
            NIGHT_DIR,
            action="plan" if args.dry_run else "apply",
            do_init=args.init,
            var_file=TFVARS,
            extra_env=night_terraform_env(),
        )
    if args.command == "backup-apply":
        return cmd_terraform(
            BACKUP_DIR,
            action="plan" if args.dry_run else "apply",
            do_init=args.init,
            var_file=TFVARS,
        )
    if args.command == "configure":
        return cmd_configure(dry_run=args.dry_run)
    if args.command == "backup":
        return cmd_backup(dry_run=args.dry_run)
    if args.command in {"destroy", "down"}:
        return cmd_destroy(
            dry_run=args.dry_run,
            skip_backup=args.skip_backup,
            do_init=args.init,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
