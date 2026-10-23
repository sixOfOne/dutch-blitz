"""The public repo has placeholders, not account identifiers.

These tests read the committed Terraform and the bot policy. They do not
call AWS and they do not run terraform init.
"""

import itertools
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO / "docs" / "grok-bot-dutch-blitz-policy.json"
ROOTS = (
    "terraform",
    "terraform/backup",
    "terraform/state",
    "terraform/iam-bot",
    "terraform/game-role",
)

# Actions the bot must not have. A later edit that adds one should fail here
# before it is applied.
FORBIDDEN_ACTIONS = {
    "iam:CreateUser",
    "iam:CreateAccessKey",
    "iam:UpdateAccessKey",
    "iam:DeleteAccessKey",
    "iam:CreatePolicy",
    "iam:CreatePolicyVersion",
    "iam:AttachUserPolicy",
    "iam:AttachRolePolicy",
    "iam:AttachGroupPolicy",
    "iam:PutUserPolicy",
    "iam:PutGroupPolicy",
    "iam:PutRolePolicy",
    "iam:DeleteRolePolicy",
    "iam:GetRolePolicy",
    "iam:CreateRole",
    "iam:DeleteRole",
    "iam:UpdateRole",
    "iam:UpdateAssumeRolePolicy",
    "iam:CreateInstanceProfile",
    "iam:DeleteInstanceProfile",
    "iam:AddRoleToInstanceProfile",
    "iam:RemoveRoleFromInstanceProfile",
    "iam:DeleteUser",
    "s3:DeleteBucket",
    "s3:DeleteObjectVersion",
    "s3:PutBucketPolicy",
    "ec2:CreateKeyPair",
    "ec2:DeleteKeyPair",
    "ec2:ImportKeyPair",
    "ec2:AssociateIamInstanceProfile",
    "ec2:DisassociateIamInstanceProfile",
    "ec2:ReplaceIamInstanceProfileAssociation",
    "ssm:StartSession",
    "ssm:PutParameter",
    "ssm:DeleteParameter",
    "ssm:GetParameter",
    "ssm:GetParameters",
    "route53:CreateHostedZone",
    "route53:DeleteHostedZone",
    "route53:ChangeTagsForResource",
    "budgets:ModifyBudget",
    "dynamodb:PutItem",
    "dynamodb:DeleteItem",
}


# Example values the variable defaults still use. The reference JSON no
# longer contains them: templatefile inserts each ${name} in one pass.
# 123456789012 is AWS's documented example account id, not this account.
EXAMPLE_VALUES = {
    "aws_account_id": "000000000000",
    "aws_region": "us-east-2",
    "state_bucket_name": "dutch-blitz-tfstate-replace-me",
    "backup_bucket_name": "dutch-blitz-scores-replace-me",
    "dns_zone_id": "Z000000000000EXAMPLE",
    "dns_record_name": "blitz",
    "role_name": "dutch-blitz-game",
    "budget_name": "dutch-blitz-monthly",
}
REAL_VALUES = {
    **EXAMPLE_VALUES,
    "aws_account_id": "123456789012",
    "state_bucket_name": "example-tfstate-bucket",
    "backup_bucket_name": "example-scores-bucket",
    "dns_zone_id": "Z1234567890ABC",
}
TEMPLATE_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _render(values: dict[str, str]) -> str:
    """One-pass stand-in for templatefile. Inserted text is not scanned again."""
    text = POLICY_PATH.read_text(encoding="utf-8")
    found = set(TEMPLATE_RE.findall(text))
    missing = found - values.keys()
    extra = values.keys() - found
    if missing or extra:
        raise AssertionError(
            f"template vars mismatch missing={sorted(missing)} extra={sorted(extra)}"
        )

    def repl(match: re.Match[str]) -> str:
        return values[match.group(1)]

    return TEMPLATE_RE.sub(repl, text)


def _policy():
    return json.loads(_render(EXAMPLE_VALUES))


def _actions(policy: dict) -> set[str]:
    found = set()
    for statement in policy["Statement"]:
        action = statement["Action"]
        if isinstance(action, str):
            found.add(action)
        else:
            found.update(action)
    return found


def test_reference_policy_uses_non_overlapping_template_variables():
    text = POLICY_PATH.read_text(encoding="utf-8")
    policy = json.loads(text)
    assert policy["Version"] == "2012-10-17"
    names = set(TEMPLATE_RE.findall(text))
    assert names == set(EXAMPLE_VALUES)
    for left, right in itertools.permutations(names, 2):
        assert left not in right
    # The old zone token contains the account token. Neither literal is
    # in the reference file, so a later replace() cannot corrupt the zone.
    assert "000000000000" not in text
    assert "Z000000000000EXAMPLE" not in text
    assert "replace-me" not in text
    assert "EXAMPLE" not in text
    # The alert address is a tfvars variable, not a policy field.
    assert "@" not in text
    rendered = _render(EXAMPLE_VALUES)
    assert json.loads(rendered)["Version"] == "2012-10-17"
    assert "Z000000000000EXAMPLE" in rendered
    assert "arn:aws:iam::000000000000:role/dutch-blitz-game" in rendered
    assert "blitz.*" in rendered


def test_rendered_policy_has_no_placeholder_fragments():
    rendered = _render(REAL_VALUES)
    policy = json.loads(rendered)
    for fragment in ("EXAMPLE", "000000000000", "replace-me", "${"):
        assert fragment not in rendered
    zones = [
        item
        for statement in policy["Statement"]
        for item in (
            statement["Resource"]
            if isinstance(statement["Resource"], list)
            else [statement["Resource"]]
        )
        if isinstance(item, str) and "hostedzone/" in item
    ]
    assert zones
    assert set(zones) == {"arn:aws:route53:::hostedzone/Z1234567890ABC"}
    assert "arn:aws:iam::123456789012:role/dutch-blitz-game" in rendered
    assert "key-pair/" not in rendered
    # The historical bug left this shape: account id substituted inside
    # the zone token, and the zone replace then found nothing.
    assert "hostedzone/Z123456789012EXAMPLE" not in rendered


def test_zone_value_containing_the_account_token_is_inserted_whole():
    values = dict(EXAMPLE_VALUES)
    values["aws_account_id"] = "123456789012"
    values["dns_zone_id"] = "Z000000000000EXAMPLE"
    rendered = _render(values)
    assert "arn:aws:route53:::hostedzone/Z000000000000EXAMPLE" in rendered
    assert "hostedzone/Z123456789012EXAMPLE" not in rendered
    assert "arn:aws:iam::123456789012:role/dutch-blitz-game" in rendered


def test_iam_bot_renders_with_templatefile_and_rejects_leftover_fragments():
    iam = (REPO / "terraform" / "iam-bot" / "main.tf").read_text(encoding="utf-8")
    assert "templatefile(" in iam
    assert "docs/grok-bot-dutch-blitz-policy.json" in iam
    assert "bot_policy_json = replace(" not in iam
    for name in EXAMPLE_VALUES:
        assert name in iam
    assert 'strcontains(local.bot_policy_json, "EXAMPLE")' in iam
    assert 'strcontains(local.bot_policy_json, "000000000000")' in iam
    assert 'strcontains(local.bot_policy_json, "replace-me")' in iam
    assert "!var.enable_aws" in iam
    # description is ForceNew. The applied sentence stays so a plan updates
    # the document in place. SSM is in the policy JSON, not this string.
    assert (
        'description = "EC2 and PassRole for the pre-existing game role, '
        'for dutch_blitz_host up and down."' in iam
    )


def test_bot_policy_is_split_under_the_managed_policy_quota():
    # IAM ignores whitespace. Inline user policies stop at 2,048 characters.
    # Managed policies stop at 6,144. The document is over the inline limit,
    # so terraform/iam-bot attaches two policies even when the whole file
    # fits in one managed policy. The cut is the PassRole statement:
    # compute is that statement and everything before it.
    policy = _policy()
    compact = json.dumps(policy, separators=(",", ":"))
    assert len(compact) > 2048
    sids = [statement["Sid"] for statement in policy["Statement"]]
    cut = sids.index("PassGameRoleToEc2Only") + 1
    for half in (policy["Statement"][:cut], policy["Statement"][cut:]):
        encoded = json.dumps(
            {"Version": "2012-10-17", "Statement": half},
            separators=(",", ":"),
        )
        assert len(encoded) <= 6144
    assert "ChangeOnlyBlitzARecord" in sids[cut:]
    assert "LockGameNightState" in sids[cut:]
    assert "ReadGameInstanceProfile" in sids[:cut]


def test_modify_instance_attribute_is_only_shutdown_behavior():
    # Provider v6 calls this after RunInstances. The grant is one attribute,
    # on the tagged instance, in the game region. Nothing else moves.
    policy = _policy()
    by_sid = {statement["Sid"]: statement for statement in policy["Statement"]}
    statement = by_sid["SetShutdownBehaviorOnTaggedGameInstance"]
    assert statement["Effect"] == "Allow"
    assert statement["Action"] == "ec2:ModifyInstanceAttribute"
    assert statement["Resource"] == "arn:aws:ec2:us-east-2:000000000000:instance/*"
    assert statement["Condition"]["StringEquals"] == {
        "aws:RequestedRegion": "us-east-2",
        "aws:ResourceTag/Project": "dutch-blitz",
        "ec2:Attribute/InstanceInitiatedShutdownBehavior": "terminate",
    }
    actions = _actions(policy)
    assert "ec2:ModifyInstanceAttribute" in actions
    assert "ec2:AssociateIamInstanceProfile" not in actions
    assert "ec2:DisassociateIamInstanceProfile" not in actions
    assert "ec2:ReplaceIamInstanceProfileAssociation" not in actions
    iam = (REPO / "terraform" / "iam-bot" / "main.tf").read_text(encoding="utf-8")
    assert "SetShutdownBehaviorOnTaggedGameInstance" in iam
    # The new statement is in the compute half, before PassRole, so the
    # managed-policy split still matches the Sid set.
    sids = [item["Sid"] for item in policy["Statement"]]
    assert sids.index("SetShutdownBehaviorOnTaggedGameInstance") < sids.index(
        "PassGameRoleToEc2Only"
    )


def test_ssm_run_command_is_tagged_instances_and_one_document():
    policy = _policy()
    by_sid = {statement["Sid"]: statement for statement in policy["Statement"]}
    instance = by_sid["SendCommandToTaggedGameInstance"]
    assert instance["Effect"] == "Allow"
    assert instance["Action"] == "ssm:SendCommand"
    assert instance["Resource"] == "arn:aws:ec2:us-east-2:000000000000:instance/*"
    assert instance["Condition"]["StringEquals"] == {
        "aws:RequestedRegion": "us-east-2",
        "aws:ResourceTag/Project": "dutch-blitz",
    }
    document = by_sid["SendCommandWithRunShellScript"]
    assert document["Action"] == "ssm:SendCommand"
    assert document["Resource"] == "arn:aws:ssm:us-east-2::document/AWS-RunShellScript"
    assert document["Condition"]["StringEquals"] == {"aws:RequestedRegion": "us-east-2"}
    reads = by_sid["ReadSsmCommandResults"]
    assert set(reads["Action"]) == {
        "ssm:GetCommandInvocation",
        "ssm:ListCommandInvocations",
        "ssm:DescribeInstanceInformation",
    }
    assert reads["Resource"] == "*"
    assert reads["Condition"]["StringEquals"] == {"aws:RequestedRegion": "us-east-2"}
    actions = _actions(policy)
    assert "ssm:StartSession" not in actions
    assert "ssm:*" not in actions
    assert "ssm:PutParameter" not in actions
    assert "key-pair/" not in POLICY_PATH.read_text(encoding="utf-8")
    sids = [item["Sid"] for item in policy["Statement"]]
    assert sids.index("ReadSsmCommandResults") < sids.index("PassGameRoleToEc2Only")
    bot = (REPO / "docs" / "bot-iam.md").read_text(encoding="utf-8")
    assert "lets the bot run root commands on tagged instances" in bot


def test_game_role_ssm_is_an_inline_subset():
    role = (REPO / "terraform" / "game-role" / "main.tf").read_text(encoding="utf-8")
    assert "AmazonSSMManagedInstanceCore" in role
    assert "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" not in role
    assert "ssm:UpdateInstanceInformation" in role
    assert "ssmmessages:CreateControlChannel" in role
    assert "ssmmessages:CreateDataChannel" in role
    assert "ssmmessages:OpenControlChannel" in role
    assert "ssmmessages:OpenDataChannel" in role
    assert "ec2messages:AcknowledgeMessage" in role
    assert "ec2messages:GetMessages" in role
    assert "ec2messages:SendReply" in role
    assert "ssm:GetDocument" in role
    assert "document/AWS-RunShellScript" in role
    assert "ssm:GetParameter" not in role
    assert "ssm:GetParameters" not in role
    assert 'actions   = ["ssm:StartSession"]' not in role
    assert "ssm:StartSession" in role


def _lock_provider_block(text: str, name: str) -> str:
    marker = f"registry.terraform.io/hashicorp/{name}"
    start = text.index(marker)
    rest = text[start:]
    next_provider = rest.find('\nprovider "', 1)
    if next_provider == -1:
        return rest
    return rest[:next_provider]


def test_provider_lockfiles_are_committed():
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    ignored = [
        line.strip()
        for line in gitignore.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert ".terraform.lock.hcl" not in ignored
    assert ".terraform/" in ignored
    dependabot = (REPO / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert ".terraform.lock.hcl" in dependabot
    assert 'package-ecosystem: "terraform"' in dependabot
    for root in ROOTS:
        assert f'"/{root}"' in dependabot
        text = (REPO / root / ".terraform.lock.hcl").read_text(encoding="utf-8")
        assert "registry.terraform.io/hashicorp/aws" in text
        assert "registry.terraform.io/hashicorp/null" in text
        # One h1 hash per platform requested at lock time: linux_amd64,
        # darwin_arm64, darwin_amd64. zh hashes are the signed checksums.
        assert _lock_provider_block(text, "aws").count("h1:") >= 3
        assert _lock_provider_block(text, "null").count("h1:") >= 3


def test_bot_policy_refuses_admin_and_bucket_delete():
    policy = _policy()
    actions = _actions(policy)
    assert FORBIDDEN_ACTIONS.isdisjoint(actions)
    assert "*" not in actions
    assert "ce:GetCostAndUsage" in actions
    assert "budgets:ViewBudget" in actions
    assert "iam:PassRole" in actions
    assert "iam:GetInstanceProfile" in actions
    iam_actions = {action for action in actions if action.startswith("iam:")}
    assert iam_actions == {"iam:PassRole", "iam:GetInstanceProfile"}
    assert "route53:ChangeResourceRecordSets" in actions


def test_route53_changes_are_limited_to_blitz_and_clerk_is_denied():
    policy = _policy()
    by_sid = {statement["Sid"]: statement for statement in policy["Statement"]}
    allow = by_sid["ChangeOnlyBlitzARecord"]
    assert allow["Condition"]["ForAllValues:StringLike"][
        "route53:ChangeResourceRecordSetsNormalizedRecordNames"
    ] == ["blitz.*"]
    assert allow["Condition"]["ForAllValues:StringEquals"][
        "route53:ChangeResourceRecordSetsRecordTypes"
    ] == ["A"]
    deny_names = by_sid["DenyClerkAndMailRecordChanges"]["Condition"]["ForAnyValue:StringLike"][
        "route53:ChangeResourceRecordSetsNormalizedRecordNames"
    ]
    assert "clerk.*" in deny_names
    assert "accounts.*" in deny_names
    assert "clkmail.*" in deny_names
    denied_types = by_sid["DenyZoneDelegationAndCnameChanges"]["Condition"][
        "ForAnyValue:StringEquals"
    ]["route53:ChangeResourceRecordSetsRecordTypes"]
    assert denied_types == ["NS", "SOA", "CNAME"]
    passed = by_sid["PassGameRoleToEc2Only"]
    assert passed["Action"] == "iam:PassRole"
    assert passed["Resource"] == "arn:aws:iam::000000000000:role/dutch-blitz-game"
    assert passed["Condition"]["StringEquals"]["iam:PassedToService"] == "ec2.amazonaws.com"
    profile = by_sid["ReadGameInstanceProfile"]
    assert profile["Action"] == "iam:GetInstanceProfile"
    assert profile["Resource"] == "arn:aws:iam::000000000000:instance-profile/dutch-blitz-game"


BACKUP_REFRESH_READS = {
    "s3:GetAccelerateConfiguration",
    "s3:GetBucketAcl",
    "s3:GetBucketCORS",
    "s3:GetBucketLogging",
    "s3:GetBucketObjectLockConfiguration",
    "s3:GetBucketOwnershipControls",
    "s3:GetBucketPolicy",
    "s3:GetBucketRequestPayment",
    "s3:GetBucketWebsite",
    "s3:GetLifecycleConfiguration",
    "s3:GetReplicationConfiguration",
}


def test_backup_bucket_refresh_reads_are_scoped_to_that_bucket():
    policy = _policy()
    text = POLICY_PATH.read_text(encoding="utf-8")
    assert "s3:GetObjectLockConfiguration" not in text
    by_sid = {statement["Sid"]: statement for statement in policy["Statement"]}
    reads = by_sid["ReadBackupBucketConfiguration"]
    assert set(reads["Action"]) == BACKUP_REFRESH_READS
    assert reads["Effect"] == "Allow"
    assert reads["Resource"] == "arn:aws:s3:::dutch-blitz-scores-replace-me"
    manage = by_sid["ManageBackupBucketConfiguration"]
    writes = {
        action
        for action in manage["Action"]
        if action.startswith(("s3:Put", "s3:Delete")) or action == "s3:CreateBucket"
    }
    assert writes == {
        "s3:CreateBucket",
        "s3:PutBucketTagging",
        "s3:PutBucketVersioning",
        "s3:PutEncryptionConfiguration",
        "s3:PutBucketPublicAccessBlock",
    }
    actions = _actions(policy)
    assert "s3:DeleteBucket" not in actions
    assert "s3:PutBucketPolicy" not in actions
    assert "s3:DeleteObject" not in reads["Action"]
    assert "s3:GetObject" not in reads["Action"]


def test_instance_tags_the_volume_without_replacing_it():
    night = (REPO / "terraform" / "main.tf").read_text(encoding="utf-8")
    assert "volume_tags = {" in night
    assert re.search(r"user_data\s+=\s+local\.boot_script", night)
    assert "user_data_replace_on_change = false" in night
    assert "ignore_changes = [user_data, key_name]" in night
    assert "ssh_authorized_keys" not in night
    assert 'resource "aws_security_group_rule" "ssh"' not in night
    assert 'templatefile("${path.module}/templates/cloud-init.sh.tftpl"' in night
    start = night.index("root_block_device {")
    block = night[start : night.index("\n  }", start)]
    assert "tags" not in block
    variables = (REPO / "terraform" / "variables.tf").read_text(encoding="utf-8")
    assert 'variable "extra_ssh_public_keys"' in variables
    assert "ssh-ed25519" in variables
    assert "ssh-rsa" in variables
    for root in ("terraform/backup/variables.tf", "terraform/game-role/variables.tf"):
        assert 'variable "extra_ssh_public_keys"' in (REPO / root).read_text(encoding="utf-8")
    example = (REPO / "terraform" / "terraform.tfvars.example").read_text(encoding="utf-8")
    assert "extra_ssh_public_keys = []" in example


def test_state_delete_is_only_the_lock_object():
    policy = _policy()
    by_sid = {statement["Sid"]: statement for statement in policy["Statement"]}
    lock = by_sid["LockGameNightState"]
    assert lock["Action"] == ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    assert all(resource.endswith(".tflock") for resource in lock["Resource"])
    state = by_sid["ReadWriteGameNightState"]
    assert "s3:DeleteObject" not in state["Action"]
    assert all(resource.endswith(".tfstate") for resource in state["Resource"])


def test_every_root_uses_s3_lockfiles_and_ci_skips_the_backend():
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "\nbackend.hcl\n" in gitignore
    example = (REPO / "terraform" / "backend.hcl.example").read_text(encoding="utf-8")
    assert "dutch-blitz-tfstate-replace-me" in example
    assert "000000000000" not in example
    for root in ROOTS:
        text = (REPO / root / "main.tf").read_text(encoding="utf-8")
        assert 'backend "s3"' in text
        assert "use_lockfile = true" in text
        assert "encrypt" in text
        assert "dynamodb_table" not in text
        assert "dynamodb:" not in text
        assert root in workflow or (
            root == "terraform" and "terraform terraform/backup" in workflow
        )
    assert "terraform/state" in workflow
    assert "terraform/iam-bot" in workflow
    assert "terraform/game-role" in workflow
    assert "init -backend=false" in workflow
    assert "\n*_override.tf\n" in gitignore
    doc = (REPO / "docs" / "remote-state.md").read_text(encoding="utf-8")
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    state_main = (REPO / "terraform" / "state" / "main.tf").read_text(encoding="utf-8")
    assert "bootstrap_override.tf" in doc
    assert 'backend "local" {}' in doc
    assert "Backend initialization required" in doc
    assert "rm terraform/state/bootstrap_override.tf" in doc
    assert "init -migrate-state -backend-config=../backend.hcl" in doc
    assert "terraform show" in doc
    assert "dutch-blitz-tfstate-backup" in doc
    # init -backend=false then apply is not the bootstrap procedure.
    assert "init -backend=false -input=false\nterraform -chdir=terraform/state apply" not in doc
    assert "init -backend=false -input=false" not in state_main
    assert "bootstrap_override.tf" in state_main
    assert "bootstrap_override.tf" in readme
    assert 'backend "local" {}' in readme
    iam = (REPO / "terraform" / "iam-bot" / "main.tf").read_text(encoding="utf-8")
    assert "docs/grok-bot-dutch-blitz-policy.json" in iam
    assert 'resource "aws_iam_access_key"' not in iam
    assert "ReadGameInstanceProfile" in iam
    assert "ManageGameRole" not in iam
    assert "AdjustInstanceAndAttachProfile" not in iam


def test_game_role_is_owner_only_and_night_stack_only_reads_it():
    policy = POLICY_PATH.read_text(encoding="utf-8")
    assert "game-role/terraform.tfstate" not in policy
    night = (REPO / "terraform" / "main.tf").read_text(encoding="utf-8")
    assert 'resource "aws_iam_role"' not in night
    assert 'resource "aws_iam_role_policy"' not in night
    assert 'resource "aws_iam_instance_profile"' not in night
    assert 'data "aws_iam_instance_profile" "game"' in night
    assert "from = aws_iam_role.game\n" in night
    assert "from = aws_iam_role_policy.backup_bucket\n" in night
    assert "from = aws_iam_instance_profile.game\n" in night
    assert night.count("destroy = false") >= 3
    role = (REPO / "terraform" / "game-role" / "main.tf").read_text(encoding="utf-8")
    assert 'resource "aws_iam_role" "game"' in role
    assert 'resource "aws_iam_instance_profile" "game"' in role
    assert "dutch-blitz/game-role/terraform.tfstate" in role
    assert "use_lockfile = true" in role
