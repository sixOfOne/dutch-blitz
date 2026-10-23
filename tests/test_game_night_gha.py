"""Game night workflow and the OIDC deployer role.

These tests read committed files and call the CLI with fakes. They do
not call AWS and they do not run terraform init.
"""

import re
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from dutch_blitz_host import cli as host_cli
from dutch_blitz_host.ssm import describe_ping_status

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "terraform" / "gha-deployer"
WORKFLOW = REPO / ".github" / "workflows" / "game-night.yml"
SUBJECT = "repo:sixOfOne@50211093/dutch-blitz@1413603165:environment:game-night"
WORKFLOW_REF = "sixOfOne/dutch-blitz/.github/workflows/game-night.yml@refs/heads/main"
INSTANCE = "i-0123456789abcdef0"


def _root_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(ROOT.glob("*.tf")))


def test_deployer_trust_is_exact_and_reuses_the_bot_policies():
    text = _root_text()
    assert "*" not in text
    assert "StringLike" not in text
    assert "ForAllValues" not in text
    assert "ForAnyValue" not in text
    assert "sts:AssumeRoleWithWebIdentity" in text
    assert "sts:TagSession" not in text
    assert 'sts:AssumeRole"' not in text
    assert SUBJECT in text
    assert "sts.amazonaws.com" in text
    assert "workflow_dispatch" in text
    assert "refs/heads/main" in text
    assert WORKFLOW_REF in text
    assert "max_session_duration = 3600" in text
    assert re.search(r'name\s+=\s+"dutch-blitz-gha-deployer"', text)
    assert "dutch-blitz/gha-deployer/terraform.tfstate" in text
    assert 'data "aws_iam_policy" "compute"' in text
    assert 'data "aws_iam_policy" "records"' in text
    assert re.search(r'name\s+=\s+"\$\{var\.bot_user_name\}-compute"', text)
    assert re.search(r'name\s+=\s+"\$\{var\.bot_user_name\}-records"', text)
    assert "docs/grok-bot-dutch-blitz-policy.json" in text
    assert 'resource "aws_iam_policy"' not in text
    assert "templatefile" not in text
    assert "ssm:SendCommand" not in text
    assert "aws_iam_access_key" not in text
    assert 'data "tls_certificate" "github"' in text
    policy = (REPO / "docs" / "grok-bot-dutch-blitz-policy.json").read_text(encoding="utf-8")
    assert "gha-deployer" not in policy
    assert "repo:sixOfOne/dutch-blitz:environment:game-night" not in text


def test_game_night_workflow_is_dispatch_only_and_stores_no_keys():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"^permissions:\s*\{\}\s*$", text, re.MULTILINE)
    assert "workflow_dispatch:" in text
    assert "pull_request:" not in text
    assert "\n  push:" not in text
    assert "group: game-night" in text
    assert "cancel-in-progress: false" in text
    assert "environment: game-night" in text
    assert "runs-on: ubuntu-latest" in text
    assert "timeout-minutes: 50" in text
    assert "id-token: write" in text
    assert "contents: read" in text
    assert "contents: write" not in text
    assert "packages: write" not in text
    assert "vars.AWS_ROLE_ARN" in text
    assert "secrets.AWS" not in text
    assert "aws-access-key-id" not in text
    assert "AWS_ACCESS_KEY" not in text
    assert "AWS_SECRET_ACCESS_KEY" not in text
    assert "secrets.CLERK_PUBLISHABLE_KEY" in text
    assert "secrets.CLERK_SECRET_KEY" in text
    assert "secrets.TERRAFORM_TFVARS" in text
    assert "secrets.BACKEND_HCL" in text
    assert "runner.temp" in text
    assert "RUNNER_TEMP" in text
    assert "0o600" in text
    assert "requirements-host.txt" in text
    assert "TF_VAR_app_image" in text
    assert "DUTCH_BLITZ_TFVARS" in text
    assert "DUTCH_BLITZ_BACKEND_CONFIG" in text
    assert "set -x" not in text
    assert "role-duration-seconds: 3600" in text
    assert "audience: sts.amazonaws.com" in text
    assert "sha-[0-9a-f]{7,40}" in text
    pins = re.findall(r"uses: \S+@([0-9a-f]{40}) # v\d+\.\d+\.\d+", text)
    assert len(pins) == 4
    assert len(set(pins)) == 4


def test_docs_list_jeremys_setup_in_order():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    bot = (REPO / "docs" / "bot-iam.md").read_text(encoding="utf-8")
    security = (REPO / "docs" / "repo-security.md").read_text(encoding="utf-8")
    for text in (readme, bot, security):
        assert "terraform/gha-deployer" in text
        assert "AWS_ROLE_ARN" in text
        assert "game-night" in text
        assert "Required reviewers" in text
        assert "CLERK_SECRET_KEY" in text
        assert "TERRAFORM_TFVARS" in text
        assert "BACKEND_HCL" in text
        assert SUBJECT in text
        assert "apply -var-file=terraform.tfvars" in text
    assert "terraform/gha-deployer" in agents
    assert "permissions: {}" in agents
    assert "AWS_ROLE_ARN" in agents


def test_tfvars_and_backend_paths_follow_the_environment(tmp_path, monkeypatch):
    override = tmp_path / "night.tfvars"
    override.write_text('enable_aws = true\naws_region = "us-east-2"\n', encoding="utf-8")
    other = tmp_path / "other.tfvars"
    other.write_text("enable_aws = false\n", encoding="utf-8")
    monkeypatch.setenv("DUTCH_BLITZ_TFVARS", str(override))
    assert host_cli._tfvars_file() == override
    assert host_cli.load_tfvars()["enable_aws"] == "true"
    assert host_cli.load_tfvars(other)["enable_aws"] == "false"
    backend = tmp_path / "backend.hcl"
    backend.write_text('bucket = "example-tfstate"\nregion = "us-east-2"\n', encoding="utf-8")
    monkeypatch.setenv("DUTCH_BLITZ_BACKEND_CONFIG", str(backend))
    assert host_cli.require_backend_config() == backend
    explicit = tmp_path / "explicit.hcl"
    explicit.write_text('bucket = "explicit"\n', encoding="utf-8")
    assert host_cli.require_backend_config(explicit) == explicit
    source = Path(host_cli.__file__).read_text(encoding="utf-8")
    assert "var_file=TFVARS" not in source


def test_status_refuses_cloud_init_and_prints_the_ping(monkeypatch, capsys):
    monkeypatch.setattr(
        host_cli,
        "load_tfvars",
        lambda: {"enable_aws": "true", "aws_region": "us-east-2"},
    )
    monkeypatch.setattr(host_cli, "_init_terraform", lambda *_args, **_kwargs: 0)
    requested: list[str] = []
    original_output = host_cli._terraform_output_raw

    def raw(_tf_dir: Path, name: str) -> str:
        requested.append(name)
        if name == "instance_id":
            return INSTANCE
        return "shown"

    monkeypatch.setattr(host_cli, "_terraform_output_raw", raw)
    seen: dict[str, str] = {}

    def ping(instance_id: str, region: str, *, client: object = None) -> str:
        seen["id"] = instance_id
        seen["region"] = region
        assert client is None
        return "Online"

    monkeypatch.setattr(host_cli, "describe_ping_status", ping)
    assert host_cli.cmd_status(do_init=False) == 0
    assert requested == list(host_cli._STATUS_OUTPUTS)
    assert "cloud_init" not in requested
    out = capsys.readouterr().out
    assert "ssm_ping: Online" in out
    assert "cloud_init" not in out
    assert (
        "-json"
        not in Path(host_cli.cmd_status.__code__.co_filename)
        .read_text(encoding="utf-8")
        .split("def cmd_status", 1)[1]
        .split("\ndef output_value", 1)[0]
    )
    assert seen == {"id": INSTANCE, "region": "us-east-2"}
    with pytest.raises(SystemExit, match="Clerk keys"):
        original_output(REPO, "cloud_init")


def test_status_reports_a_denied_ping_without_the_error_body(monkeypatch, capsys):
    monkeypatch.setattr(host_cli, "load_tfvars", lambda: {"enable_aws": "true"})
    monkeypatch.setattr(host_cli, "_init_terraform", lambda *_args, **_kwargs: 0)

    def raw(_tf_dir: Path, name: str) -> str:
        return INSTANCE if name == "instance_id" else ""

    monkeypatch.setattr(host_cli, "_terraform_output_raw", raw)

    def denied(*_args, **_kwargs):
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "secret-body"}},
            "DescribeInstanceInformation",
        )

    monkeypatch.setattr(host_cli, "describe_ping_status", denied)
    assert host_cli.cmd_status(do_init=False) == 0
    out = capsys.readouterr().out
    assert "ssm_ping: unavailable (AccessDenied)" in out
    assert "secret-body" not in out


def test_status_when_aws_is_off_does_not_init(monkeypatch, capsys):
    monkeypatch.setattr(host_cli, "load_tfvars", lambda: {"enable_aws": "false"})

    def boom(*_args, **_kwargs):
        raise AssertionError("status must not init when AWS is off")

    monkeypatch.setattr(host_cli, "_init_terraform", boom)
    assert host_cli.main(["status"]) == 0
    assert "no game-night instance" in capsys.readouterr().out


def test_describe_ping_status_reads_the_matching_instance():
    class _Fake:
        def describe_instance_information(self, **_kwargs):
            return {
                "InstanceInformationList": [
                    {"InstanceId": INSTANCE, "PingStatus": "Online"},
                ]
            }

    assert describe_ping_status(INSTANCE, "us-east-2", client=_Fake()) == "Online"
