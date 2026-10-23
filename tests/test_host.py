"""Controller logic for a game-night host. No Terraform and no AWS calls."""

import json
import socket
import sqlite3
import struct
from datetime import UTC, datetime
from pathlib import Path

import pytest

import dutch_blitz_host.cli as host_cli
from dutch_blitz_host import host_backup
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
    REMOTE_BOOT_COMMAND,
    aws_enabled,
    build_parser,
    clerk_terraform_env,
    parse_tfvars,
    require_backend_config,
    resolve_ssh_key,
    run_remote_script,
    ssh_argv,
    ssh_probe_argv,
    terraform_init_argv,
    terraform_local_init_argv,
    wait_for_ssh,
)
from dutch_blitz_host.dns import (
    a_records_from_doh,
    a_records_from_route53,
    a_records_match,
    answers_unusable,
    decode_records,
    encode_name,
    resolve_dns_mode,
    route53_a_records,
    wait_for_a_record,
)
from dutch_blitz_host.envfile import (
    ENV_FILE_MODE,
    assert_deployable,
    deployment_env,
    merged_env,
    redact,
)
from dutch_blitz_host.hostname import dns_fqdn, hostname_for, https_origin
from dutch_blitz_host.instance_status import (
    LiveInstance,
    StateGameInstance,
    describe_instances_argv,
    describe_tagged_game_instances_argv,
    parse_describe_instances,
    parse_described_instances,
    parse_game_instance_show,
    pick_tagged_instance,
    should_skip_ssh_backup,
)


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
    earlier = backup_object_name(datetime(2026, 10, 10, 1, 2, 3, tzinfo=UTC))
    later = backup_object_name(datetime(2026, 10, 10, 4, 5, 6, tzinfo=UTC))
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
    assert destroy_plan(skip_backup=False, instance_gone=True) == [
        "skip-backup:instance-gone",
        "terraform-destroy:terraform",
    ]
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
    assert "DUTCH_BLITZ_HOST=0.0.0.0\n" in text
    assert "DUTCH_BLITZ_ACTIVITY_PATH=/var/lib/dutch-blitz/last-activity\n" in text
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


def test_tfvars_and_ssh_command_do_not_carry_secrets():
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
    env = clerk_terraform_env(
        {
            "CLERK_PUBLISHABLE_KEY": "pk_test_publishable",
            "CLERK_SECRET_KEY": "sk_test_secret",
        }
    )
    assert env["TF_VAR_clerk_secret_key"] == "sk_test_secret"
    command = " ".join(
        ssh_argv("203.0.113.10", Path("/home/owner/.ssh/neo-atari.pem"), REMOTE_BOOT_COMMAND)
    )
    assert "sudo bash -s" in command
    assert "sk_test_secret" not in command
    assert "pk_test_publishable" not in command


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
    argv = ssh_probe_argv("203.0.113.10", Path("/k.pem"))
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

    assert wait_for_ssh(
        "203.0.113.10",
        Path("/k.pem"),
        timeout=60,
        interval=5,
        run=run,
        sleep=clock.sleep,
        clock=clock,
    )
    assert len(calls) == 3
    assert clock.now == 10


def test_route53_name_is_the_fqdn_and_sslip_stays_when_unset():
    assert (
        hostname_for(
            "203.0.113.10",
            dns_zone_name="sixofonedev.com",
            dns_record_name="blitz",
        )
        == "blitz.sixofonedev.com"
    )
    assert (
        hostname_for(
            "203.0.113.10",
            "scores.example.com",
            dns_zone_name="SixOfOneDev.com.",
            dns_record_name="Blitz.",
        )
        == "blitz.sixofonedev.com"
    )
    assert https_origin("blitz.sixofonedev.com") == "https://blitz.sixofonedev.com"
    assert hostname_for("203.0.113.10", dns_zone_name="  ") == "203.0.113.10.sslip.io"
    assert (
        hostname_for("203.0.113.10", "scores.example.com", dns_zone_name="") == "scores.example.com"
    )
    assert dns_fqdn(None, "blitz") is None


def test_route53_name_rejects_urls_and_clerk_labels():
    with pytest.raises(ValueError):
        hostname_for("203.0.113.10", dns_zone_name="https://sixofonedev.com")
    with pytest.raises(ValueError):
        hostname_for("203.0.113.10", dns_zone_name="sixofonedev.com", dns_record_name="clerk")
    with pytest.raises(ValueError):
        hostname_for(
            "203.0.113.10", dns_zone_name="sixofonedev.com", dns_record_name="clk._domainkey"
        )
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


def test_benchmark_and_empty_answers_are_unusable():
    assert answers_unusable([])
    assert answers_unusable(None)
    assert answers_unusable(["198.18.0.1"])
    assert answers_unusable(["198.18.0.0"])
    assert answers_unusable(["198.19.255.255"])
    assert not answers_unusable(["198.17.255.255"])
    assert not answers_unusable(["198.20.0.1"])
    assert not answers_unusable(["198.51.100.8"])
    assert not answers_unusable(["203.0.113.10"])


def test_classic_mismatch_does_not_use_doh():
    clock = _FakeClock()

    def fetch(_url, _timeout):
        raise AssertionError("port 53 returned a real address")

    client = _ScriptedDns([["198.51.100.8"]])
    assert not wait_for_a_record(
        "blitz.sixofonedev.com",
        "203.0.113.10",
        zone="sixofonedev.com",
        timeout=0,
        interval=5,
        client=client,
        sleep=clock.sleep,
        clock=clock,
        doh_fetch=fetch,
        route53_lookup=lambda *_args: None,
    )
    assert clock.now == 0


def test_intercepted_port_53_uses_doh_and_route53():
    clock = _FakeClock()
    client = _ScriptedDns([["198.18.0.1"], ["198.18.0.1"], ["198.18.0.1"]])
    looked_up = []

    def fetch(url, _timeout):
        assert url.startswith("https://dns.google/resolve?")
        return {"Status": 0, "Answer": [{"type": 1, "data": "203.0.113.10"}]}

    def route53(hostname, zone):
        looked_up.append((hostname, zone))
        return ["203.0.113.10"]

    assert wait_for_a_record(
        "blitz.sixofonedev.com",
        "203.0.113.10",
        zone="sixofonedev.com",
        timeout=30,
        interval=5,
        client=client,
        sleep=clock.sleep,
        clock=clock,
        doh_fetch=fetch,
        route53_lookup=route53,
    )
    assert clock.now == 0
    assert looked_up == [("blitz.sixofonedev.com", "sixofonedev.com")]


def test_empty_classic_answer_uses_doh():
    clock = _FakeClock()
    client = _ScriptedDns([[], [], []])

    def fetch(url, _timeout):
        assert "dns.google" in url
        return {"Status": 0, "Answer": [{"type": 1, "data": "203.0.113.10"}]}

    assert wait_for_a_record(
        "blitz.sixofonedev.com",
        "203.0.113.10",
        zone="sixofonedev.com",
        timeout=30,
        interval=5,
        client=client,
        sleep=clock.sleep,
        clock=clock,
        doh_fetch=fetch,
        route53_lookup=lambda *_args: None,
    )
    assert clock.now == 0


def test_doh_mode_skips_port_53_and_falls_back_to_cloudflare(monkeypatch):
    monkeypatch.setenv("DUTCH_BLITZ_DNS_MODE", "doh")
    calls = []

    class _Boom:
        def ns_records(self, *_args, **_kwargs):
            raise AssertionError("classic lookup")

        def a_records(self, *_args, **_kwargs):
            raise AssertionError("classic lookup")

    def fetch(url, _timeout):
        calls.append(url)
        if "dns.google" in url:
            raise OSError("dns.google unreachable")
        assert url.startswith("https://cloudflare-dns.com/dns-query?")
        return {"Status": 0, "Answer": [{"type": 1, "data": "203.0.113.10"}]}

    assert wait_for_a_record(
        "blitz.sixofonedev.com",
        "203.0.113.10",
        zone="sixofonedev.com",
        timeout=10,
        interval=5,
        client=_Boom(),
        mode=None,
        doh_fetch=fetch,
        route53_lookup=lambda *_args: ["203.0.113.10"],
    )
    assert any("dns.google" in url for url in calls)
    assert any("cloudflare-dns.com" in url for url in calls)


def test_doh_keeps_a_real_google_answer_and_route53_can_block_it():
    def google_only(url, _timeout):
        assert "dns.google" in url
        return {"Status": 0, "Answer": [{"type": 1, "data": "198.51.100.8"}]}

    assert not wait_for_a_record(
        "blitz.sixofonedev.com",
        "203.0.113.10",
        zone="sixofonedev.com",
        timeout=0,
        interval=5,
        mode="doh",
        doh_fetch=google_only,
        route53_lookup=lambda *_args: ["203.0.113.10"],
    )

    def both(url, _timeout):
        if "dns.google" in url:
            return {"Status": 0, "Answer": [{"type": 1, "data": "198.18.0.1"}]}
        return {"Status": 0, "Answer": [{"type": 1, "data": "203.0.113.10"}]}

    assert not wait_for_a_record(
        "blitz.sixofonedev.com",
        "203.0.113.10",
        zone="sixofonedev.com",
        timeout=0,
        interval=5,
        mode="doh",
        doh_fetch=both,
        route53_lookup=lambda *_args: ["198.51.100.8"],
    )
    assert wait_for_a_record(
        "blitz.sixofonedev.com",
        "203.0.113.10",
        zone="sixofonedev.com",
        timeout=0,
        interval=5,
        mode="doh",
        doh_fetch=both,
        route53_lookup=lambda *_args: None,
    )


def test_doh_json_and_route53_listing():
    assert a_records_from_doh(
        {
            "Status": 0,
            "Answer": [{"type": 1, "data": "203.0.113.10"}, {"type": 5, "data": "other."}],
        }
    ) == ["203.0.113.10"]
    assert a_records_from_doh({"Status": 3}) == []
    sets = [
        {
            "Name": "clerk.sixofonedev.com.",
            "Type": "CNAME",
            "ResourceRecords": [{"Value": "accounts.example.com"}],
        },
        {
            "Name": "blitz.sixofonedev.com.",
            "Type": "A",
            "ResourceRecords": [{"Value": "203.0.113.10"}],
        },
    ]
    assert a_records_from_route53("blitz.sixofonedev.com", sets) == ["203.0.113.10"]
    assert a_records_from_route53("missing.sixofonedev.com", sets) == []
    with pytest.raises(ValueError, match="classic or doh"):
        resolve_dns_mode("udp")


class _AwsResult:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


def test_route53_lookup_reads_the_zone_and_stops_when_aws_fails():
    pages = iter(
        [
            {
                "HostedZones": [
                    {
                        "Id": "/hostedzone/ZPRIVATE",
                        "Name": "sixofonedev.com.",
                        "Config": {"PrivateZone": True},
                    },
                    {
                        "Id": "/hostedzone/Z123",
                        "Name": "sixofonedev.com.",
                        "Config": {"PrivateZone": False},
                    },
                ]
            },
            {
                "IsTruncated": True,
                "NextRecordName": "blitz.sixofonedev.com.",
                "NextRecordType": "A",
                "ResourceRecordSets": [
                    {
                        "Name": "clerk.sixofonedev.com.",
                        "Type": "CNAME",
                        "ResourceRecords": [{"Value": "x"}],
                    },
                ],
            },
            {
                "IsTruncated": False,
                "ResourceRecordSets": [
                    {
                        "Name": "blitz.sixofonedev.com.",
                        "Type": "A",
                        "ResourceRecords": [{"Value": "203.0.113.10"}],
                    },
                ],
            },
        ]
    )
    commands = []

    def run(argv, **_kwargs):
        commands.append(argv)
        return _AwsResult(0, json.dumps(next(pages)))

    assert route53_a_records("blitz.sixofonedev.com", "sixofonedev.com", run=run) == [
        "203.0.113.10"
    ]
    assert commands[0][1:3] == ["route53", "list-hosted-zones-by-name"]
    assert commands[1][1:3] == ["route53", "list-resource-record-sets"]
    assert "--hosted-zone-id" in commands[1]
    assert commands[1][commands[1].index("--hosted-zone-id") + 1] == "Z123"
    assert "--start-record-name" in commands[2]

    def failed(argv, **_kwargs):
        return _AwsResult(255, "")

    assert route53_a_records("blitz.sixofonedev.com", "sixofonedev.com", run=failed) is None


def test_ssh_key_flag_env_and_pem_agree(monkeypatch):
    monkeypatch.delenv("DUTCH_BLITZ_SSH_KEY", raising=False)
    for command in ("up", "configure", "backup", "down", "destroy"):
        assert build_parser().parse_args([command]).ssh_key is None
    assert resolve_ssh_key(None) == str(Path.home() / ".ssh" / "neo-atari.pem")
    monkeypatch.setenv("DUTCH_BLITZ_SSH_KEY", "~/keys/bot.pem")
    assert resolve_ssh_key(None) == str(Path.home() / "keys" / "bot.pem")
    assert resolve_ssh_key("/tmp/other.pem") == "/tmp/other.pem"
    assert (
        build_parser().parse_args(["up", "--ssh-key", "/tmp/flag.pem"]).ssh_key == "/tmp/flag.pem"
    )


def test_main_uses_the_same_ssh_key_for_up_configure_backup_and_down(monkeypatch):
    monkeypatch.setenv("DUTCH_BLITZ_SSH_KEY", "/keys/from-env.pem")
    seen = {}

    def remember(name):
        def fake(**kwargs):
            seen[name] = kwargs["ssh_key"]
            return 0

        return fake

    monkeypatch.setattr(host_cli, "cmd_up", remember("up"))
    monkeypatch.setattr(host_cli, "cmd_configure", remember("configure"))
    monkeypatch.setattr(host_cli, "cmd_backup", remember("backup"))
    monkeypatch.setattr(host_cli, "cmd_destroy", remember("down"))
    assert host_cli.main(["up"]) == 0
    assert host_cli.main(["configure", "--dry-run"]) == 0
    assert host_cli.main(["backup", "--dry-run"]) == 0
    assert host_cli.main(["down", "--ssh-key", "/keys/flag.pem", "--dry-run"]) == 0
    assert seen == {
        "up": "/keys/from-env.pem",
        "configure": "/keys/from-env.pem",
        "backup": "/keys/from-env.pem",
        "down": "/keys/flag.pem",
    }


def test_bad_dns_mode_stops_configure(monkeypatch):
    def boom(*_args, **_kwargs):
        raise ValueError("DUTCH_BLITZ_DNS_MODE must be classic or doh, got 'udp'.")

    monkeypatch.setattr(host_cli, "wait_for_a_record", boom)
    with pytest.raises(SystemExit, match="classic or doh"):
        host_cli._dns_ready(
            {"dns_zone_name": "sixofonedev.com", "dns_record_name": "blitz"},
            hostname="blitz.sixofonedev.com",
            public_ip="203.0.113.10",
            dry_run=False,
        )


def test_wait_for_ssh_gives_up_after_the_timeout():
    clock = _FakeClock()
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return _Result(255)

    assert not wait_for_ssh(
        "203.0.113.10",
        Path("/k.pem"),
        timeout=20,
        interval=5,
        run=run,
        sleep=clock.sleep,
        clock=clock,
    )
    assert clock.now <= 20
    assert len(calls) == 5


def test_describe_instances_treats_not_found_as_gone_and_errors_as_unknown():
    argv = describe_instances_argv("i-0123456789abcdef0", "us-east-2")
    assert argv[:3] == ["aws", "ec2", "describe-instances"]
    assert "--instance-ids" in argv
    state, failed = parse_describe_instances('["terminated"]\n', "", 0)
    assert (state, failed) == ("terminated", False)
    assert should_skip_ssh_backup(state, describe_failed=failed) is True
    missing, missing_failed = parse_describe_instances(
        "",
        "An error occurred (InvalidInstanceID.NotFound) when calling the DescribeInstances operation",
        254,
    )
    assert missing is None and missing_failed is False
    assert should_skip_ssh_backup(missing, describe_failed=missing_failed) is True
    shutting, _shut_failed = parse_describe_instances('["shutting-down"]\n', "", 0)
    assert should_skip_ssh_backup(shutting, describe_failed=False) is True
    running, _run_failed = parse_describe_instances('["running"]\n', "", 0)
    assert should_skip_ssh_backup(running, describe_failed=False) is False
    # A stopped disk may still hold scores. Do not skip the backup.
    stopped, _stopped_failed = parse_describe_instances('["stopped"]\n', "", 0)
    assert should_skip_ssh_backup(stopped, describe_failed=False) is False
    _unknown, unknown_failed = parse_describe_instances("", "Unable to locate credentials", 255)
    assert unknown_failed is True
    assert should_skip_ssh_backup(None, describe_failed=True) is False
    assert parse_describe_instances("[]\n", "", 0) == (None, False)


def test_down_skips_ssh_backup_when_the_instance_already_terminated(monkeypatch, capsys):
    monkeypatch.setattr(
        host_cli, "load_tfvars", lambda: {"enable_aws": "true", "aws_region": "us-east-2"}
    )
    monkeypatch.setattr(
        host_cli,
        "terraform_outputs",
        lambda _path: {"instance_id": {"value": "i-0123456789abcdef0"}},
    )
    monkeypatch.setattr(
        host_cli, "describe_instance_state", lambda *_args, **_kwargs: ("terminated", False)
    )
    called = {"backup": 0, "destroy": 0}

    def backup(**_kwargs):
        called["backup"] += 1
        return 0

    def destroy(*_args, **kwargs):
        called["destroy"] += 1
        assert kwargs["action"] == "destroy"
        return 0

    monkeypatch.setattr(host_cli, "cmd_backup", backup)
    monkeypatch.setattr(host_cli, "cmd_terraform", destroy)
    assert (
        host_cli.cmd_destroy(ssh_key="/k.pem", dry_run=False, skip_backup=False, do_init=False) == 0
    )
    assert called == {"backup": 0, "destroy": 1}
    assert "Skipping the SSH backup" in capsys.readouterr().out


def test_down_still_backs_up_when_describe_fails(monkeypatch):
    monkeypatch.setattr(host_cli, "load_tfvars", lambda: {"enable_aws": "true"})
    monkeypatch.setattr(
        host_cli,
        "terraform_outputs",
        lambda _path: {"instance_id": {"value": "i-0123456789abcdef0"}},
    )
    monkeypatch.setattr(host_cli, "describe_instance_state", lambda *_args, **_kwargs: (None, True))
    called = {"backup": 0}

    def backup(**_kwargs):
        called["backup"] += 1
        return 0

    monkeypatch.setattr(host_cli, "cmd_backup", backup)
    monkeypatch.setattr(host_cli, "cmd_terraform", lambda *_args, **_kwargs: 0)
    assert (
        host_cli.cmd_destroy(ssh_key="/k.pem", dry_run=False, skip_backup=False, do_init=False) == 0
    )
    assert called["backup"] == 1


def _running_reservation(instance_id: str, public_ip: str, state: str = "running") -> str:
    body = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": instance_id,
                        "PublicIpAddress": public_ip,
                        "State": {"Name": state},
                    }
                ]
            }
        ]
    }
    return json.dumps(body)


def test_show_json_keeps_a_tainted_instance_with_no_id():
    tainted = {
        "values": {
            "root_module": {
                "resources": [
                    {
                        "address": "aws_instance.game[0]",
                        "mode": "managed",
                        "type": "aws_instance",
                        "name": "game",
                        "tainted": True,
                        "values": {"id": None, "public_ip": ""},
                    }
                ]
            }
        }
    }
    parsed = parse_game_instance_show(tainted)
    assert parsed == StateGameInstance(None, None, True)
    identified = parse_game_instance_show(
        {
            "values": {
                "root_module": {
                    "resources": [
                        {
                            "mode": "managed",
                            "type": "aws_instance",
                            "name": "game",
                            "tainted": True,
                            "values": {
                                "id": "i-0123456789abcdef0",
                                "public_ip": "203.0.113.10",
                            },
                        }
                    ]
                }
            }
        }
    )
    assert identified == StateGameInstance("i-0123456789abcdef0", "203.0.113.10", True)
    assert parse_game_instance_show({"values": {"root_module": {"resources": []}}}) is None


def test_tagged_describe_parses_one_live_instance_and_rejects_a_bad_filter():
    argv = describe_tagged_game_instances_argv("us-east-2")
    assert argv[:3] == ["aws", "ec2", "describe-instances"]
    assert "Name=tag:Project,Values=dutch-blitz" in argv
    assert not any(part.startswith("Name=tag:Name,") for part in argv)
    instances, failed = parse_described_instances(
        _running_reservation("i-0123456789abcdef0", "203.0.113.10"),
        "",
        0,
    )
    assert failed is False
    assert instances == [LiveInstance("i-0123456789abcdef0", "running", "203.0.113.10")]
    gone, gone_failed = parse_described_instances(
        _running_reservation("i-0123456789abcdef0", "203.0.113.10", "terminated"),
        "",
        0,
    )
    assert gone_failed is False
    assert pick_tagged_instance(gone).instance is None
    missing, missing_failed = parse_described_instances(
        "",
        "InvalidInstanceID.NotFound",
        254,
    )
    assert missing == [] and missing_failed is False
    _none, errored = parse_described_instances("", "Unable to locate credentials", 255)
    assert errored is True
    choice = pick_tagged_instance(
        [
            LiveInstance("i-0123456789abcdef0", "running", "203.0.113.10"),
            LiveInstance("i-0123456789abcdef1", "running", "203.0.113.11"),
        ]
    )
    assert choice.ambiguous is True and choice.instance is None
    named = pick_tagged_instance(
        [
            LiveInstance("i-0123456789abcdef0", "running", "203.0.113.10", "other"),
            LiveInstance("i-0123456789abcdef1", "running", "203.0.113.11", "dutch-blitz-game"),
        ],
        name_tag="dutch-blitz-game",
    )
    assert named.ambiguous is False
    assert named.instance is not None
    assert named.instance.instance_id == "i-0123456789abcdef1"


def test_down_backs_up_a_tainted_instance_found_by_tag(monkeypatch):
    monkeypatch.setattr(
        host_cli, "load_tfvars", lambda: {"enable_aws": "true", "aws_region": "us-east-2"}
    )
    monkeypatch.setattr(host_cli, "terraform_outputs", lambda _path: {})
    monkeypatch.setattr(
        host_cli,
        "game_instance_from_state",
        lambda: (StateGameInstance(None, None, True), False),
    )
    monkeypatch.setattr(
        host_cli,
        "find_instances_by_tag",
        lambda _region: (
            [LiveInstance("i-0123456789abcdef0", "running", "203.0.113.10")],
            False,
        ),
    )
    seen = {}

    def backup(**kwargs):
        seen["host"] = kwargs.get("host")
        return 0

    destroyed = {"called": False}

    def destroy(*_args, **kwargs):
        destroyed["called"] = kwargs["action"] == "destroy"
        return 0

    monkeypatch.setattr(host_cli, "cmd_backup", backup)
    monkeypatch.setattr(host_cli, "cmd_terraform", destroy)
    assert (
        host_cli.cmd_destroy(ssh_key="/k.pem", dry_run=False, skip_backup=False, do_init=False) == 0
    )
    assert seen["host"] == "203.0.113.10"
    assert destroyed["called"] is True


def test_down_uses_the_state_id_when_outputs_have_none(monkeypatch):
    monkeypatch.setattr(host_cli, "load_tfvars", lambda: {"enable_aws": "true"})
    monkeypatch.setattr(host_cli, "terraform_outputs", lambda _path: {})
    monkeypatch.setattr(
        host_cli,
        "game_instance_from_state",
        lambda: (StateGameInstance("i-0123456789abcdef0", None, True), False),
    )
    monkeypatch.setattr(
        host_cli, "describe_instance_state", lambda *_args, **_kwargs: ("running", False)
    )
    monkeypatch.setattr(
        host_cli,
        "describe_instance_public_ip",
        lambda *_args, **_kwargs: "203.0.113.20",
    )
    seen = {}

    def backup(**kwargs):
        seen["host"] = kwargs.get("host")
        return 0

    monkeypatch.setattr(host_cli, "cmd_backup", backup)
    monkeypatch.setattr(host_cli, "cmd_terraform", lambda *_args, **_kwargs: 0)
    assert (
        host_cli.cmd_destroy(ssh_key="/k.pem", dry_run=False, skip_backup=False, do_init=False) == 0
    )
    assert seen["host"] == "203.0.113.20"


def test_down_skips_backup_when_state_has_no_instance(monkeypatch, capsys):
    monkeypatch.setattr(host_cli, "load_tfvars", lambda: {"enable_aws": "true"})
    monkeypatch.setattr(
        host_cli,
        "terraform_outputs",
        lambda _path: {"instance_id": {"value": None}},
    )
    monkeypatch.setattr(host_cli, "game_instance_from_state", lambda: (None, False))

    def backup(**_kwargs):
        raise AssertionError("backup")

    monkeypatch.setattr(host_cli, "cmd_backup", backup)
    monkeypatch.setattr(host_cli, "cmd_terraform", lambda *_args, **_kwargs: 0)
    assert (
        host_cli.cmd_destroy(ssh_key="/k.pem", dry_run=False, skip_backup=False, do_init=False) == 0
    )
    assert "Skipping the SSH backup" in capsys.readouterr().out


def test_down_refuses_to_guess_among_tagged_instances(monkeypatch):
    monkeypatch.setattr(host_cli, "load_tfvars", lambda: {"enable_aws": "true"})
    monkeypatch.setattr(host_cli, "terraform_outputs", lambda _path: {})
    monkeypatch.setattr(
        host_cli,
        "game_instance_from_state",
        lambda: (StateGameInstance(None, None, True), False),
    )
    monkeypatch.setattr(
        host_cli,
        "find_instances_by_tag",
        lambda _region: (
            [
                LiveInstance("i-0123456789abcdef0", "running", "203.0.113.10"),
                LiveInstance("i-0123456789abcdef1", "running", "203.0.113.11"),
            ],
            False,
        ),
    )

    def boom(**_kwargs):
        raise AssertionError("backup")

    def destroy(*_args, **_kwargs):
        raise AssertionError("destroyed")

    monkeypatch.setattr(host_cli, "cmd_backup", boom)
    monkeypatch.setattr(host_cli, "cmd_terraform", destroy)
    assert (
        host_cli.cmd_destroy(ssh_key="/k.pem", dry_run=False, skip_backup=False, do_init=False) == 1
    )


def test_down_refuses_to_destroy_when_the_final_backup_fails(monkeypatch):
    monkeypatch.setattr(host_cli, "load_tfvars", lambda: {"enable_aws": "true"})
    monkeypatch.setattr(
        host_cli,
        "instance_already_gone",
        lambda _tfvars: host_cli.FinalBackup(False, "instance is running"),
    )
    monkeypatch.setattr(host_cli, "cmd_backup", lambda **_kwargs: 1)

    def destroy(*_args, **_kwargs):
        raise AssertionError("destroyed")

    monkeypatch.setattr(host_cli, "cmd_terraform", destroy)
    assert (
        host_cli.cmd_destroy(ssh_key="/k.pem", dry_run=False, skip_backup=False, do_init=False) == 1
    )


def test_remote_boot_script_is_stdin_not_an_argument():
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = argv
        seen["input"] = kwargs.get("input")
        return _Result(0)

    script = "#!/bin/bash\necho sk_test_secret\n"
    assert run_remote_script("203.0.113.10", Path("/k.pem"), script, run=run) == 0
    assert seen["argv"][-1] == "sudo bash -s"
    assert "sk_test_secret" not in " ".join(seen["argv"])
    assert seen["input"] == script


def test_configure_dry_run_does_not_print_or_apply(monkeypatch, capsys):
    monkeypatch.setattr(
        host_cli,
        "load_controller_env",
        lambda: {
            "CLERK_PUBLISHABLE_KEY": "pk_test_publishable",
            "CLERK_SECRET_KEY": "sk_test_secret",
            "CLERK_DEV_BYPASS": "0",
        },
    )
    monkeypatch.setattr(host_cli, "load_tfvars", lambda: {"dns_zone_name": ""})
    monkeypatch.setattr(
        host_cli,
        "terraform_outputs",
        lambda _path: {
            "mode": {"value": "aws"},
            "public_ip": {"value": "203.0.113.10"},
            "hostname": {"value": "203.0.113.10.sslip.io"},
            "backup_bucket": {"value": "dutch-blitz-scores-example"},
        },
    )

    def applied(*_args, **_kwargs):
        raise AssertionError("configure dry-run applied terraform")

    monkeypatch.setattr(host_cli, "cmd_terraform", applied)
    assert host_cli.cmd_configure(ssh_key="/nope.pem", dry_run=True) == 0
    out = capsys.readouterr().out
    assert "sk_test_secret" not in out
    assert "sudo bash -s" in out
    assert "Dry run only" in out


def test_up_passes_clerk_keys_only_to_the_night_stack(monkeypatch, tmp_path):
    key = tmp_path / "id.pem"
    key.write_text("not-a-real-key\n", encoding="utf-8")
    monkeypatch.setattr(host_cli, "load_tfvars", lambda: {"enable_aws": "true"})
    monkeypatch.setattr(
        host_cli,
        "load_controller_env",
        lambda: {
            "CLERK_PUBLISHABLE_KEY": "pk_test_publishable",
            "CLERK_SECRET_KEY": "sk_test_secret",
            "CLERK_DEV_BYPASS": "0",
        },
    )
    calls = []

    def terraform(tf_dir, **kwargs):
        calls.append((str(tf_dir), kwargs.get("extra_env")))
        return 0

    monkeypatch.setattr(host_cli, "cmd_terraform", terraform)
    monkeypatch.setattr(
        host_cli,
        "terraform_outputs",
        lambda _path: {
            "mode": {"value": "aws"},
            "public_ip": {"value": "203.0.113.10"},
            "hostname": {"value": "203.0.113.10.sslip.io"},
            "backup_bucket": {"value": "dutch-blitz-scores-example"},
            "app_image": {"value": "ghcr.io/sixofone/dutch-blitz:sha-0123abc"},
        },
    )
    monkeypatch.setattr(host_cli, "wait_for_ssh", lambda *_args, **_kwargs: True)
    seen = {}

    def cloud_init(*_args, **kwargs):
        seen["require_success"] = kwargs.get("require_success")
        return True

    monkeypatch.setattr(host_cli, "wait_for_cloud_init", cloud_init)
    assert host_cli.cmd_up(do_init=False, ssh_key=str(key)) == 0
    assert calls[0][1] is None
    assert calls[0][0].endswith("terraform/backup")
    assert calls[1][0].endswith("terraform")
    night_env = calls[1][1]
    assert night_env is not None
    assert night_env["TF_VAR_clerk_secret_key"] == "sk_test_secret"
    assert night_env["TF_VAR_clerk_publishable_key"] == "pk_test_publishable"
    assert seen["require_success"] is True


def test_bot_policy_idle_shutdown_does_not_grant_sns():
    # The instance publishes with its own role. up and down do not call SNS.
    # ModifyInstanceAttribute is present only for shutdown behavior; see
    # test_modify_instance_attribute_is_only_shutdown_behavior.
    text = (
        Path(__file__).resolve().parents[1] / "docs" / "grok-bot-dutch-blitz-policy.json"
    ).read_text(encoding="utf-8")
    assert "sns:" not in text
    assert "ec2:Describe*" in text
