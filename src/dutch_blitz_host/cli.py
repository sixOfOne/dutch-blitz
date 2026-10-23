"""Game-night commands: apply, configure, backup, and destroy.

Local-first: with ``enable_aws`` left false, apply only records that AWS is
off. A night sets ``enable_aws = true`` in the gitignored tfvars file.

The instance boots from cloud-init. ``up`` waits for that script. ``configure``
applies the game-night stack and pipes the current script over SSH, which is
how a second run retries S3 or picks up a new image pin without replacing
the instance.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

from dutch_blitz_host.backup import BACKUP_STACK, NIGHT_STACK, destroy_plan
from dutch_blitz_host.dns import wait_for_a_record
from dutch_blitz_host.envfile import assert_deployable, merged_env, redact
from dutch_blitz_host.hostname import dns_fqdn, https_origin
from dutch_blitz_host.instance_status import (
    describe_instances_argv,
    parse_describe_instances,
    should_skip_ssh_backup,
    valid_instance_id,
    valid_region,
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
SSH_USER = "ec2-user"
CLOUD_INIT_WAIT_COMMAND = "sudo cloud-init status --wait"
REMOTE_BOOT_COMMAND = "sudo bash -s"
CLOUD_INIT_WAIT_TIMEOUT_SECONDS = 1200
SSH_KEY_DEFAULT = Path.home() / ".ssh" / "neo-atari.pem"
REMOTE_BACKUP = "sudo /usr/local/bin/dutch-blitz-backup backup"
SSH_WAIT_TIMEOUT_SECONDS = 300
SSH_WAIT_INTERVAL_SECONDS = 5
SSH_PROBE_CONNECT_TIMEOUT_SECONDS = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Host a Dutch Blitz game night on EC2")
    subparsers = parser.add_subparsers(dest="command", required=True)

    up = subparsers.add_parser(
        "up", help="Create the bucket if needed, start the instance, configure it, print the URL"
    )
    _add_ssh_key(up)
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
        help="Re-run the cloud-init boot script over SSH (retry S3, or apply a new image pin)",
    )
    configure.add_argument("--dry-run", action="store_true", help="print the SSH command only")
    _add_ssh_key(configure)

    backup = subparsers.add_parser("backup", help="Upload a consistent SQLite snapshot to S3")
    _add_ssh_key(backup)
    backup.add_argument("--dry-run", action="store_true", help="print the SSH command only")

    destroy = subparsers.add_parser(
        "destroy",
        help="Back up the score book, then terraform destroy the instance. The S3 bucket stays.",
    )
    _add_ssh_key(destroy)
    destroy.add_argument("--dry-run", action="store_true", help="print the destroy plan only")
    destroy.add_argument(
        "--skip-backup",
        action="store_true",
        help="Destroy even if the instance cannot be reached. Scores since the last upload are lost.",
    )
    _add_init(destroy)

    down = subparsers.add_parser("down", help="Same as destroy")
    _add_ssh_key(down)
    down.add_argument("--dry-run", action="store_true")
    down.add_argument("--skip-backup", action="store_true")
    _add_init(down)
    return parser


def _add_ssh_key(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ssh-key",
        default=None,
        help="SSH private key for ec2-user. Default: DUTCH_BLITZ_SSH_KEY, or ~/.ssh/neo-atari.pem.",
    )


def resolve_ssh_key(explicit: str | None) -> str:
    """Flag, then DUTCH_BLITZ_SSH_KEY, then ~/.ssh/neo-atari.pem."""
    if explicit is not None and explicit.strip():
        return str(Path(explicit).expanduser())
    env = os.environ.get("DUTCH_BLITZ_SSH_KEY", "").strip()
    if env:
        return str(Path(env).expanduser())
    return str(SSH_KEY_DEFAULT)


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


def ssh_argv(host: str, key: Path, remote_command: str) -> list[str]:
    return [
        "ssh",
        "-i",
        str(key),
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "BatchMode=yes",
        f"{SSH_USER}@{host}",
        remote_command,
    ]


def backup_ssh_argv(host: str, key: Path) -> list[str]:
    return ssh_argv(host, key, REMOTE_BACKUP)


def ssh_probe_argv(host: str, key: Path) -> list[str]:
    """A no-op SSH login, used to tell when a new instance accepts the key."""
    argv = ssh_argv(host, key, "true")
    return argv[:-2] + ["-o", f"ConnectTimeout={SSH_PROBE_CONNECT_TIMEOUT_SECONDS}"] + argv[-2:]


def wait_for_ssh(
    host: str,
    key: Path,
    *,
    timeout: float = SSH_WAIT_TIMEOUT_SECONDS,
    interval: float = SSH_WAIT_INTERVAL_SECONDS,
    run: Any = subprocess.run,
    sleep: Any = time.sleep,
    clock: Any = time.monotonic,
) -> bool:
    """Retry an SSH login until it succeeds or ``timeout`` seconds pass.

    A freshly launched instance refuses port 22 for a short while, and then
    cloud-init still has to install the authorized key.
    """
    argv = ssh_probe_argv(host, key)
    deadline = clock() + timeout
    attempt = 0
    while True:
        attempt += 1
        result = run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            print(f"SSH is ready on {host} (attempt {attempt}).")
            return True
        if clock() + interval > deadline:
            return False
        print(f"Waiting for SSH on {host} (attempt {attempt})...")
        sleep(interval)


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


def require_aws_outputs(outputs: dict) -> tuple[str, str, str]:
    mode = output_value(outputs, "mode")
    ip = output_value(outputs, "public_ip")
    hostname = output_value(outputs, "hostname")
    bucket = output_value(outputs, "backup_bucket")
    if mode != "aws" or not ip or not hostname or not bucket:
        raise SystemExit(
            "AWS host unavailable. Set enable_aws=true in terraform/terraform.tfvars "
            "and run `PYTHONPATH=src python3 -m dutch_blitz_host up`."
        )
    return ip, hostname, bucket


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


def cmd_backup(*, ssh_key: str, dry_run: bool) -> int:
    outputs = terraform_outputs(NIGHT_DIR)
    ip, _hostname, _bucket = require_aws_outputs(outputs)
    key = Path(ssh_key).expanduser()
    cmd = backup_ssh_argv(ip, key)
    print("Backup:", _fmt(cmd))
    if dry_run:
        print("Dry run only; no backup was uploaded.")
        return 0
    if not key.is_file():
        raise SystemExit(f"SSH key not found: {key}")
    return _run(cmd, REPO_ROOT)


def read_cloud_init_script(tf_dir: Path = NIGHT_DIR) -> str:
    """The rendered boot script. Stdout only. The script contains Clerk keys."""
    try:
        raw = subprocess.check_output(
            [_terraform(), "output", "-raw", "cloud_init"],
            cwd=str(tf_dir),
            text=True,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        raise SystemExit(
            "Could not read terraform output cloud_init. Apply the game-night stack first. "
            + detail
        ) from exc
    if not raw.startswith("#!/bin/bash"):
        raise SystemExit("terraform output cloud_init is not the boot script.")
    return raw


def run_remote_script(host: str, key: Path, script: str, *, run: Any = subprocess.run) -> int:
    """Pipe the boot script to root over SSH. The script is not a command argument."""
    cmd = ssh_argv(host, key, REMOTE_BOOT_COMMAND)
    print("Launch:", _fmt(cmd))
    completed = run(cmd, input=script, text=True, check=False)
    return int(completed.returncode)


def wait_for_cloud_init(
    host: str,
    key: Path,
    *,
    require_success: bool,
    run: Any = subprocess.run,
) -> bool:
    """Block until cloud-init finishes. First boot is what runs the script.

    ``require_success`` is for ``up``. ``configure`` only needs the first
    boot to be finished so a second run does not overlap it. A finished
    error still returns true in that case.
    """
    cmd = ssh_argv(host, key, CLOUD_INIT_WAIT_COMMAND)
    print("Launch:", _fmt(cmd))
    try:
        result = run(cmd, timeout=CLOUD_INIT_WAIT_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        print(
            f"cloud-init on {host} did not finish within {CLOUD_INIT_WAIT_TIMEOUT_SECONDS} seconds. "
            "SSH in and read /var/log/cloud-init-output.log, then run configure.",
            file=sys.stderr,
        )
        return False
    if result.returncode == 0:
        print(f"cloud-init finished on {host}.")
        return True
    if not require_success:
        print(f"cloud-init on {host} finished with an error. Running the boot script again.")
        return True
    print(
        f"cloud-init on {host} failed. The boot script's own log is "
        "/var/log/cloud-init-output.log on the instance. "
        "When the instance role can reach S3, run: "
        "PYTHONPATH=src python3 -m dutch_blitz_host configure",
        file=sys.stderr,
    )
    return False


def cmd_configure(*, ssh_key: str, dry_run: bool) -> int:
    """Apply the night stack, then run the current boot script on the host.

    Apply does not replace the instance. user_data changes are ignored so
    a new image pin or idle limit does not stop the machine. The script
    piped over SSH is the current render, and that is what the host runs.
    """
    env = load_controller_env()
    assert_deployable(env)
    tfvars = load_tfvars()
    outputs = terraform_outputs(NIGHT_DIR)
    ip, hostname, _bucket = require_aws_outputs(outputs)
    key = Path(ssh_key).expanduser()
    print(f"Hostname: {hostname}")
    print(f"Share URL: {https_origin(hostname)}")
    print("Launch:", _fmt(ssh_argv(ip, key, REMOTE_BOOT_COMMAND)))
    print("The boot script is not printed. It contains the Clerk keys.")
    if dry_run:
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
    if not key.is_file():
        raise SystemExit(f"SSH key not found: {key}")
    if not wait_for_cloud_init(ip, key, require_success=False):
        return 1
    script = read_cloud_init_script()
    return run_remote_script(ip, key, script)


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


def cmd_up(*, do_init: bool, ssh_key: str) -> int:
    tfvars = load_tfvars()
    if not aws_enabled(tfvars):
        raise SystemExit(
            "enable_aws is false, so no instance will be created. "
            "Copy terraform/terraform.tfvars.example to terraform/terraform.tfvars, "
            "set enable_aws = true, your SSH CIDR, and a backup bucket name, then re-run."
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
    ip, hostname, _bucket = require_aws_outputs(outputs)
    key = Path(ssh_key).expanduser()
    if not key.is_file():
        raise SystemExit(f"SSH key not found: {key}")
    if not wait_for_ssh(ip, key):
        print(
            f"SSH on {ip} did not accept {key} within {SSH_WAIT_TIMEOUT_SECONDS} seconds. "
            "When it does, run: "
            f"PYTHONPATH=src python3 -m dutch_blitz_host configure --ssh-key {key}",
            file=sys.stderr,
        )
        return 1
    if not _dns_ready(tfvars, hostname=hostname, public_ip=ip, dry_run=False):
        return 1
    if not wait_for_cloud_init(ip, key, require_success=True):
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


def instance_already_gone(tfvars: dict[str, str]) -> tuple[bool, str]:
    """Whether ``down`` should skip SSH because the host already terminated.

    Returns ``(gone, detail)``. ``gone`` is false when describe fails or
    the id looks wrong. Those cases still try SSH, so a blip does not
    destroy a night whose latest scores never reached S3.
    """
    outputs = terraform_outputs(NIGHT_DIR)
    instance_id = output_value(outputs, "instance_id")
    if not instance_id:
        return True, "Terraform state has no instance id"
    if not valid_instance_id(instance_id):
        return False, f"instance id {instance_id!r} is unexpected; trying the SSH backup"
    region = tfvars.get("aws_region", "").strip() or "us-east-2"
    if not valid_region(region):
        return False, "aws_region is not a region name; trying the SSH backup"
    state, failed = describe_instance_state(instance_id, region)
    if should_skip_ssh_backup(state, describe_failed=failed):
        label = state or "missing"
        return True, f"{instance_id} is {label}"
    if failed:
        return False, "could not describe the instance; trying the SSH backup"
    return False, f"instance is {state}"


def cmd_destroy(*, ssh_key: str, dry_run: bool, skip_backup: bool, do_init: bool) -> int:
    print(f"The backup bucket stack ({BACKUP_STACK}) is not destroyed.")
    print("The instance role and the idle SNS topic (terraform/game-role) are not destroyed.")
    if dry_run:
        steps = destroy_plan(skip_backup=skip_backup)
        print("Destroy plan:", ", ".join(steps))
        print(
            "If the instance already terminated itself, down skips the SSH backup "
            "and still removes the blitz DNS record and Terraform state."
        )
        print("Dry run only; nothing was destroyed.")
        return 0
    tfvars = load_tfvars()
    if not aws_enabled(tfvars):
        print("enable_aws is false. There is no game-night instance to destroy.")
        return 0
    gone = False
    if not skip_backup:
        gone, detail = instance_already_gone(tfvars)
        if gone:
            print(
                f"The instance is already gone ({detail}). Skipping the SSH backup. "
                "The on-server shutdown uploads a snapshot before it powers off. "
                "DNS and Terraform state are still removed."
            )
        else:
            print(f"{detail}. Uploading a final snapshot.")
            code = cmd_backup(ssh_key=ssh_key, dry_run=False)
            if code != 0:
                print(
                    "Final backup failed, so the instance was left up. "
                    "Fix SSH or S3, or re-run with --skip-backup if the host is already gone.",
                    file=sys.stderr,
                )
                return code
    steps = destroy_plan(skip_backup=skip_backup, instance_gone=gone)
    print("Destroy plan:", ", ".join(steps))
    return cmd_terraform(NIGHT_DIR, action="destroy", do_init=do_init, var_file=TFVARS)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ssh_key = resolve_ssh_key(getattr(args, "ssh_key", None))
    if args.command == "up":
        return cmd_up(do_init=args.init, ssh_key=ssh_key)
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
        return cmd_configure(ssh_key=ssh_key, dry_run=args.dry_run)
    if args.command == "backup":
        return cmd_backup(ssh_key=ssh_key, dry_run=args.dry_run)
    if args.command in {"destroy", "down"}:
        return cmd_destroy(
            ssh_key=ssh_key,
            dry_run=args.dry_run,
            skip_backup=args.skip_backup,
            do_init=args.init,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
