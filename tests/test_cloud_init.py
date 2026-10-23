"""Render the cloud-init template the same way Terraform's templatefile does.

The subset is what this template uses: ``${name}``, ``${jsonencode(name)}``,
and ``$${`` for a literal ``${``. Terraform is still what the host runs.
"""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "terraform" / "templates" / "cloud-init.sh.tftpl"
DOCKERFILE = REPO / "Dockerfile"
MAIN = REPO / "terraform" / "main.tf"
VARIABLES = REPO / "terraform" / "variables.tf"

# EC2 user data is 16 KiB before base64. Leave room so a longer pin still fits.
USER_DATA_LIMIT = 16384

_TOKEN = re.compile(r"\$\{(jsonencode\(([A-Za-z0-9_]+)\)|([A-Za-z0-9_]+))\}")


def render_cloud_init(variables: dict) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    literals: list[str] = []

    def hold(match: re.Match[str]) -> str:
        literals.append("${")
        return f"\0{len(literals) - 1}\0"

    held = re.sub(r"\$\$\{", hold, template)

    def substitute(match: re.Match[str]) -> str:
        if match.group(2):
            return json.dumps(variables[match.group(2)])
        return str(variables[match.group(3)])

    rendered = _TOKEN.sub(substitute, held)
    if "${" in rendered:
        start = rendered.index("${")
        raise AssertionError(
            f"unrendered interpolation left in cloud-init: {rendered[start : start + 40]!r}"
        )
    for index, literal in enumerate(literals):
        rendered = rendered.replace(f"\0{index}\0", literal)
    return rendered


def _sample(**overrides: object) -> dict:
    values: dict = {
        "app_image": "ghcr.io/sixofone/dutch-blitz:sha-0123abc",
        "clerk_publishable_key": "pk_test_publishable",
        "clerk_secret_key": "sk_test_secret",
        "app_hostname": "blitz.sixofonedev.com",
        "acme_email": "",
        "backup_bucket": "dutch-blitz-scores-example",
        "idle_warn_minutes": 60,
        "idle_shutdown_minutes": 90,
        "uptime_warn_minutes": 420,
        "uptime_shutdown_minutes": 480,
        "idle_snooze_minutes": 120,
        "idle_check_minutes": 15,
        "idle_topic_name": "dutch-blitz-idle",
        "aws_region": "us-east-2",
    }
    values.update(overrides)
    return values


def test_rendered_boot_script_pins_the_image_and_keeps_tls_and_backups():
    script = render_cloud_init(_sample(acme_email="certs@example.com"))
    assert script.startswith("#!/bin/bash\n")
    assert 'APP_IMAGE="ghcr.io/sixofone/dutch-blitz:sha-0123abc"' in script
    assert ":latest" not in script
    assert 'CLERK_PUBLISHABLE_KEY="pk_test_publishable"' in script
    assert 'CLERK_SECRET_KEY="sk_test_secret"' in script
    assert "CLERK_DEV_BYPASS=0" in script
    assert "DUTCH_BLITZ_HOST=0.0.0.0" in script
    assert "DUTCH_BLITZ_ACTIVITY_PATH=$ACTIVITY_PATH" in script
    assert "CLERK_AUTHORIZED_PARTIES=https://$APP_HOSTNAME" in script
    assert "--restart unless-stopped" in script
    assert "-p 127.0.0.1:8000:8000" in script
    assert "caddy_2.11.7_linux_amd64.tar.gz" in script
    assert "a7a433a1b133efc3c8d10eb0b99d52a24b5ef5c322dc77f5282182b1c0402139" in script
    assert "email $ACME_EMAIL" in script
    assert "reverse_proxy 127.0.0.1:8000" in script
    assert "/var/log/caddy/access.log" in script
    assert "PasswordAuthentication" not in script
    assert "systemctl disable --now sshd" in script
    assert "sshd_config" not in script
    assert "read_configure_env" in script
    assert "/etc/dutch-blitz/configure.env" in script
    assert "systemctl enable --now amazon-ssm-agent" in script
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("echo "):
            assert "CLERK_" not in stripped
            assert "sk_test_secret" not in stripped
    assert 'BACKUP_BUCKET="dutch-blitz-scores-example"' in script
    assert "dutch-blitz-backup backup" in script
    assert "dutch-blitz-backup restore" in script
    assert script.index("dutch-blitz-backup backup") < script.index("dutch-blitz-backup restore")
    assert "dutch-blitz-idle" in script
    assert 'IDLE_TOPIC_NAME="dutch-blitz-idle"' in script
    assert "OnUnitActiveSec=$(printf '%smin' \"$IDLE_CHECK_MINUTES\")" in script
    assert "APP_UID=10001" in script
    assert len(script.encode("utf-8")) < USER_DATA_LIMIT - 800


def test_empty_acme_email_and_sslip_hostname_omit_the_global_block():
    script = render_cloud_init(_sample(app_hostname="", acme_email=""))
    assert 'APP_HOSTNAME_CONFIG=""' in script
    assert 'ACME_EMAIL=""' in script
    assert "*.sslip.io" in script
    # The global Caddy block is written only when the email is non-empty.
    assert 'if [ -n "$ACME_EMAIL" ]' in script


def test_long_keys_still_fit_in_user_data():
    script = render_cloud_init(
        _sample(
            clerk_publishable_key="pk_live_" + ("a" * 180),
            clerk_secret_key="sk_live_" + ("b" * 180),
            app_image="ghcr.io/sixofone/dutch-blitz@sha256:" + ("ab" * 32),
        )
    )
    assert len(script.encode("utf-8")) < USER_DATA_LIMIT - 800
    assert ":latest" not in script


def test_dockerfile_is_a_pinned_non_root_runtime():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert text.count("FROM python:3.11-slim@sha256:") == 2
    assert "AS build" in text
    assert "USER dutchblitz" in text
    assert "--uid 10001" in text
    assert "HEALTHCHECK" in text
    assert "socket.create_connection" in text
    assert "urlopen" not in text
    assert "CLERK_" not in text
    assert "COPY requirements.txt" in text
    assert "COPY app " in text
    assert "pip uninstall -y pip setuptools wheel" in text
    copied = [line for line in text.splitlines() if line.startswith("COPY ")]
    assert all(".env" not in line for line in copied)


def test_terraform_rejects_a_floating_latest_tag():
    variables = VARIABLES.read_text(encoding="utf-8")
    assert 'default     = "ghcr.io/sixofone/dutch-blitz:sha-0000000"' in variables
    assert "sha-[0-9a-f]{7,40}" in variables
    assert "@sha256:[0-9a-f]{64}" in variables
    assert "Do not use latest" in variables
    night = MAIN.read_text(encoding="utf-8")
    assert "sha-0000000" in night
    assert "ignore_changes = [user_data, key_name]" in night
    assert "ssh_authorized_keys" not in night
    assert 'resource "aws_security_group_rule" "http"' in night
    assert 'resource "aws_security_group_rule" "https"' in night
    assert 'resource "aws_security_group_rule" "ssh"' not in night
    assert re.search(r"user_data\s+=\s+local\.boot_script", night)
