"""Controller logic for a game-night host. No Terraform and no AWS calls."""

import socket
import sqlite3
import struct
from datetime import datetime, timezone

import pytest

from dutch_blitz_host.backup import (
    BACKUP_PREFIX,
    backup_object_name,
    backup_sqlite,
    destroy_plan,
    latest_backup_name,
    s3_cp_argv,
    s3_ls_argv,
    s3_uri,
)
from dutch_blitz_host.cli import (
    aws_enabled,
    build_parser,
    parse_tfvars,
    render_aws_inventory,
    require_backend_config,
    ssh_probe_argv,
    terraform_init_argv,
    terraform_local_init_argv,
    wait_for_ssh,
)
from dutch_blitz_host.envfile import (
    ENV_FILE_MODE,
    assert_deployable,
    deployment_env,
    merged_env,
    redact,
)
from dutch_blitz_host import host_backup
from dutch_blitz_host.dns import (
    a_records_match,
    decode_records,
    encode_name,
    wait_for_a_record,
)
from dutch_blitz_host.hostname import dns_fqdn, hostname_for, https_origin


def test_hostname_uses_sslip_for_an_ipv4():
    assert hostname_for("203.0.113.10") == "203.0.113.10.sslip.io"
    assert hostname_for(" 203.0.113.10 ") == "203.0.113.10.sslip.io"


def test_hostname_prefers_a_domain():
    assert hostname_for("203.0.113.10", "scores.example.com") == "scores.example.com"
    assert hostname_for("203.0.113.10", "scores.example.com.") == "scores.example.com"
    assert hostname_for("203.0.113.10", "  ") == "203.0.113.10.sslip.io"


def test_hostname_rejects_urls_and_non_ipv4():
    with pytest.raises(ValueError):
        hostname_for("203.0.113.10", "https://scores.example.com")
    with pytest.raises(ValueError):
        hostname_for("2001:db8::1")
    with pytest.raises(ValueError):
        hostname_for("")


def test_https_origin():
    assert https_origin("203.0.113.10.sslip.io") == "https://203.0.113.10.sslip.io"
    with pytest.raises(ValueError):
        https_origin("https://scores.example.com")


def test_backup_sqlite_snapshot_stays_behind_later_writes(tmp_path):
    source = tmp_path / "live.sqlite"
    connection = sqlite3.connect(source)
    connection.execute("create table scores (n integer)")
    connection.execute("insert into scores (n) values (1)")
    connection.commit()
    connection.close()

    snapshot = tmp_path / "snaps" / "copy.sqlite"
    backup_sqlite(source, snapshot)

    connection = sqlite3.connect(source)
    connection.execute("insert into scores (n) values (2)")
    connection.commit()
    connection.close()

    saved = sqlite3.connect(snapshot)
    rows = saved.execute("select n from scores order by n").fetchall()
    saved.close()
    assert rows == [(1,)]


def test_backup_object_names_sort_by_time():
    earlier = backup_object_name(datetime(2026, 10, 10, 1, 2, 3, tzinfo=timezone.utc))
    later = backup_object_name(datetime(2026, 10, 10, 4, 5, 6, tzinfo=timezone.utc))
    assert earlier == "20261010T010203Z.sqlite"
    assert later == "20261010T040506Z.sqlite"
    assert earlier < later


def test_s3_command_building():
    remote = s3_uri("score-bucket", BACKUP_PREFIX, "20261010T010203Z.sqlite")
    assert remote == "s3://score-bucket/dutch-blitz/20261010T010203Z.sqlite"
    assert s3_cp_argv("/tmp/snap.sqlite", remote) == [
        "aws",
        "s3",
        "cp",
        "/tmp/snap.sqlite",
        remote,
    ]
    assert s3_ls_argv("score-bucket", BACKUP_PREFIX) == [
        "aws",
        "s3",
        "ls",
        "s3://score-bucket/dutch-blitz/",
    ]


def test_latest_backup_name_from_aws_listing():
    listing = """
2026-10-10 01:02:03      120 20261010T010203Z.sqlite
2026-10-10 04:05:06      140 20261010T040506Z.sqlite
                           PRE other/
"""
    assert latest_backup_name(listing) == "20261010T040506Z.sqlite"
    assert latest_backup_name("\n") is None


def test_destroy_plan_backs_up_before_the_night_stack():
    steps = destroy_plan(skip_backup=False)
    assert steps == ["backup", "terraform-destroy:terraform"]
    assert destroy_plan(skip_backup=True) == ["terraform-destroy:terraform"]
    assert all("terraform/backup" not in step for step in steps)


def test_env_file_mode_and_deployment_contents():
    assert ENV_FILE_MODE == "0600"
    text = deployment_env(
        publishable_key="pk_test_publishable",
        secret_key="sk_test_secret",
        origin="https://203.0.113.10.sslip.io",
        db_path="/var/lib/dutch-blitz/dutch_blitz.sqlite",
    )
    assert "CLERK_DEV_BYPASS=0\n" in text
    assert "CLERK_DEV_BYPASS=1" not in text
    assert "CLERK_AUTHORIZED_PARTIES=https://203.0.113.10.sslip.io\n" in text
    assert "DUTCH_BLITZ_HOST=127.0.0.1\n" in text
    with pytest.raises(ValueError):
        deployment_env(
            publishable_key="pk_test_publishable",
            secret_key="sk_test_secret",
            origin="http://203.0.113.10.sslip.io",
            db_path="/var/lib/dutch-blitz/dutch_blitz.sqlite",
        )


def test_assert_deployable_rejects_bypass_and_missing_keys():
    with pytest.raises(SystemExit):
        assert_deployable(
            {
                "CLERK_PUBLISHABLE_KEY": "pk_test_publishable",
                "CLERK_SECRET_KEY": "sk_test_secret",
                "CLERK_DEV_BYPASS": "true",
            }
        )
    with pytest.raises(SystemExit):
        assert_deployable({"CLERK_PUBLISHABLE_KEY": "pk_test_publishable"})
    assert_deployable(
        {
            "CLERK_PUBLISHABLE_KEY": "pk_test_publishable",
            "CLERK_SECRET_KEY": "sk_test_secret",
            "CLERK_DEV_BYPASS": "0",
        }
    )


def test_process_environment_wins_and_secrets_are_redacted():
    merged = merged_env(
        {"CLERK_SECRET_KEY": "from-env"},
        'CLERK_SECRET_KEY=from-file\nCLERK_PUBLISHABLE_KEY="pk_test_file"\n',
    )
    assert merged["CLERK_SECRET_KEY"] == "from-env"
    assert merged["CLERK_PUBLISHABLE_KEY"] == "pk_test_file"
    assert redact("using sk_test_secret now", ["sk_test_secret"]) == "using [redacted] now"


def test_tfvars_and_inventory_do_not_carry_secrets():
    parsed = parse_tfvars(
        """
        # comment
        enable_aws = true
        domain = ""
        backup_bucket_name = "dutch-blitz-scores-example"
        """
    )
    assert aws_enabled(parsed) is True
    assert aws_enabled({}) is False
    assert parsed["backup_bucket_name"] == "dutch-blitz-scores-example"
    inventory = render_aws_inventory(
        public_ip="203.0.113.10",
        ssh_key="/home/owner/.ssh/neo-atari.pem",
        hostname="203.0.113.10.sslip.io",
        backup_bucket="dutch-blitz-scores-example",
        acme_email="",
    )
    assert "ansible_host: 203.0.113.10" in inventory
    assert "ansible_ssh_private_key_file: /home/owner/.ssh/neo-atari.pem" in inventory
    assert "app_hostname: 203.0.113.10.sslip.io" in inventory
    assert "backup_bucket: dutch-blitz-scores-example" in inventory
    assert "sk_test_secret" not in inventory


def test_down_parser_accepts_skip_backup():
    args = build_parser().parse_args(["down", "--skip-backup"])
    assert args.command == "down"
    assert args.skip_backup is True


def test_remote_init_passes_the_gitignored_backend_file(tmp_path):
    backend = tmp_path / "backend.hcl"
    backend.write_text('bucket = "example-tfstate"\nregion = "us-east-2"\n', encoding="utf-8")
    argv = terraform_init_argv("terraform", backend)
    assert argv[:3] == ["terraform", "init", "-input=false"]
    assert f"-backend-config={backend}" in argv
    assert "-migrate-state" not in argv
    assert "-reconfigure" not in argv
    assert terraform_local_init_argv("terraform") == [
        "terraform",
        "init",
        "-backend=false",
        "-input=false",
    ]
    assert require_backend_config(backend) == backend
    with pytest.raises(SystemExit):
        require_backend_config(tmp_path / "missing.hcl")


def test_backup_database_noop_when_the_file_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DUTCH_BLITZ_DB_PATH", str(tmp_path / "missing.sqlite"))
    monkeypatch.setenv("BACKUP_BUCKET", "example-bucket")
    monkeypatch.setattr(host_backup, "BACKUP_ENV", tmp_path / "absent.env")
    assert host_backup.backup_database() == 0
    assert "nothing to upload" in capsys.readouterr().out


def test_host_backup_usage():
    assert host_backup.main([]) == 2
    assert host_backup.main(["nope"]) == 2


class _Result:
    def __init__(self, returncode):
        self.returncode = returncode


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_ssh_probe_is_a_batch_login_with_a_connect_timeout():
    argv = ssh_probe_argv("203.0.113.10", "/k.pem")
    assert argv[0] == "ssh"
    assert "BatchMode=yes" in argv
    assert any(a.startswith("ConnectTimeout=") for a in argv)
    assert argv[-2:] == ["ec2-user@203.0.113.10", "true"]


def test_wait_for_ssh_retries_until_the_login_succeeds():
    codes = iter([255, 255, 0])
    calls = []
    clock = _FakeClock()

    def run(argv, **_kwargs):
        calls.append(argv)
        return _Result(next(codes))

    assert wait_for_ssh("203.0.113.10", "/k.pem", timeout=60, interval=5,
                        run=run, sleep=clock.sleep, clock=clock)
    assert len(calls) == 3
    assert clock.now == 10


def test_route53_name_is_the_fqdn_and_sslip_stays_when_unset():
    assert hostname_for(
        "203.0.113.10",
        dns_zone_name="sixofonedev.com",
        dns_record_name="blitz",
    ) == "blitz.sixofonedev.com"
    assert hostname_for(
        "203.0.113.10",
        "scores.example.com",
        dns_zone_name="SixOfOneDev.com.",
        dns_record_name="Blitz.",
    ) == "blitz.sixofonedev.com"
    assert https_origin("blitz.sixofonedev.com") == "https://blitz.sixofonedev.com"
    assert hostname_for("203.0.113.10", dns_zone_name="  ") == "203.0.113.10.sslip.io"
    assert hostname_for("203.0.113.10", "scores.example.com", dns_zone_name="") == "scores.example.com"
    assert dns_fqdn(None, "blitz") is None


def test_route53_name_rejects_urls_and_clerk_labels():
    with pytest.raises(ValueError):
        hostname_for("203.0.113.10", dns_zone_name="https://sixofonedev.com")
    with pytest.raises(ValueError):
        hostname_for("203.0.113.10", dns_zone_name="sixofonedev.com", dns_record_name="clerk")
    with pytest.raises(ValueError):
        hostname_for("203.0.113.10", dns_zone_name="sixofonedev.com", dns_record_name="clk._domainkey")
    with pytest.raises(ValueError):
        hostname_for("203.0.113.10", dns_zone_name="sixofonedev.com", dns_record_name="")


def test_dns_answer_must_be_exactly_the_new_address():
    packet = _dns_a_packet("blitz.sixofonedev.com", ["203.0.113.10"], ident=7)
    assert decode_records(packet, 7, 1) == ["203.0.113.10"]
    stale = _dns_a_packet("blitz.sixofonedev.com", ["198.51.100.8"], ident=7)
    assert not a_records_match(decode_records(stale, 7, 1), "203.0.113.10")
    both = _dns_a_packet("blitz.sixofonedev.com", ["203.0.113.10", "198.51.100.8"], ident=4)
    assert not a_records_match(decode_records(both, 4, 1), "203.0.113.10")
    assert a_records_match(["203.0.113.10"], "203.0.113.10")
    missing = _dns_a_packet("blitz.sixofonedev.com", [], ident=7, rcode=3)
    assert decode_records(missing, 7, 1) == []


def test_wait_for_dns_keeps_polling_until_the_new_ip_replaces_the_old_one():
    clock = _FakeClock()
    client = _ScriptedDns(
        [
            ["198.51.100.8"],
            ["203.0.113.10"],
            ["198.51.100.8"],
            ["203.0.113.10"],
            ["203.0.113.10"],
            ["203.0.113.10"],
        ]
    )
    assert wait_for_a_record(
        "blitz.sixofonedev.com",
        "203.0.113.10",
        zone="sixofonedev.com",
        timeout=60,
        interval=5,
        client=client,
        sleep=clock.sleep,
        clock=clock,
    )
    assert clock.now == 10
    assert ("a", "blitz.sixofonedev.com", "192.0.2.53") in client.calls
    assert ("a", "blitz.sixofonedev.com", "1.1.1.1") in client.calls
    assert ("a", "blitz.sixofonedev.com", "8.8.8.8") in client.calls


def test_wait_for_dns_returns_immediately_when_every_server_has_the_new_ip():
    clock = _FakeClock()
    client = _ScriptedDns([["203.0.113.10"], ["203.0.113.10"], ["203.0.113.10"]])
    assert wait_for_a_record(
        "blitz.sixofonedev.com",
        "203.0.113.10",
        zone="sixofonedev.com",
        timeout=30,
        interval=5,
        client=client,
        sleep=clock.sleep,
        clock=clock,
    )
    assert clock.now == 0


def test_wait_for_dns_times_out_while_the_old_address_remains():
    clock = _FakeClock()
    client = _ScriptedDns([["198.51.100.8"]] * 8)
    assert not wait_for_a_record(
        "blitz.sixofonedev.com",
        "203.0.113.10",
        zone="sixofonedev.com",
        timeout=10,
        interval=5,
        client=client,
        sleep=clock.sleep,
        clock=clock,
    )
    assert clock.now == 10


def _dns_a_packet(name: str, addresses: list[str], *, ident: int, rcode: int = 0) -> bytes:
    question = encode_name(name) + struct.pack("!HH", 1, 1)
    answers = b""
    for address in addresses:
        answers += struct.pack("!HHHIH", 0xC00C, 1, 1, 60, 4) + socket.inet_aton(address)
    flags = 0x8180 | rcode
    header = struct.pack("!HHHHHH", ident, flags, 1, len(addresses), 0, 0)
    return header + question + answers


class _ScriptedDns:
    def __init__(self, answers: list[list[str]]):
        self._answers = list(answers)
        self.calls: list[tuple[str, str, str]] = []

    def ns_records(self, zone: str, server: str) -> list[str]:
        self.calls.append(("ns", zone, server))
        return ["ns1.example.net"]

    def a_records(self, name: str, server: str, **_kwargs: object) -> list[str]:
        self.calls.append(("a", name, server))
        if name == "ns1.example.net":
            return ["192.0.2.53"]
        return self._answers.pop(0)


def test_wait_for_ssh_gives_up_after_the_timeout():
    clock = _FakeClock()
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return _Result(255)

    assert not wait_for_ssh("203.0.113.10", "/k.pem", timeout=20, interval=5,
                            run=run, sleep=clock.sleep, clock=clock)
    assert clock.now <= 20
    assert len(calls) == 5
