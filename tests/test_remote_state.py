"""The public repo has placeholders, not account identifiers.

These tests read the committed Terraform and the bot policy. They do not
call AWS and they do not run terraform init.
"""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO / "docs" / "grok-bot-dutch-blitz-policy.json"
ROOTS = ("terraform", "terraform/backup", "terraform/state", "terraform/iam-bot")

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
    "iam:DeleteUser",
    "s3:DeleteBucket",
    "s3:DeleteObjectVersion",
    "s3:PutBucketPolicy",
    "ec2:CreateKeyPair",
    "ec2:DeleteKeyPair",
    "ec2:ImportKeyPair",
    "route53:CreateHostedZone",
    "route53:DeleteHostedZone",
    "route53:ChangeTagsForResource",
    "budgets:ModifyBudget",
    "dynamodb:PutItem",
    "dynamodb:DeleteItem",
}


def _policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _actions(policy: dict) -> set[str]:
    found = set()
    for statement in policy["Statement"]:
        action = statement["Action"]
        if isinstance(action, str):
            found.add(action)
        else:
            found.update(action)
    return found


def test_bot_policy_is_json_and_keeps_placeholders():
    text = POLICY_PATH.read_text(encoding="utf-8")
    policy = json.loads(text)
    assert policy["Version"] == "2012-10-17"
    for placeholder in (
        "000000000000",
        "dutch-blitz-tfstate-replace-me",
        "dutch-blitz-scores-replace-me",
        "Z000000000000EXAMPLE",
        "dutch-blitz-game",
        "key-pair/neo-atari",
        "blitz.*",
        "dutch-blitz-monthly",
        "us-east-2",
    ):
        assert placeholder in text
    # The alert address is a tfvars variable, not a policy field.
    assert "@" not in text


def test_bot_policy_is_split_under_the_managed_policy_quota():
    # IAM ignores whitespace. Inline user policies stop at 2,048 characters.
    # Managed policies stop at 6,144. The document is past both, so
    # terraform/iam-bot attaches two policies. The cut is the PassRole
    # statement: compute is that statement and everything before it.
    policy = _policy()
    compact = json.dumps(policy, separators=(",", ":"))
    assert len(compact) > 6144
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


def test_bot_policy_refuses_admin_and_bucket_delete():
    policy = _policy()
    actions = _actions(policy)
    assert FORBIDDEN_ACTIONS.isdisjoint(actions)
    assert "*" not in actions
    assert "ce:GetCostAndUsage" in actions
    assert "budgets:ViewBudget" in actions
    assert "iam:PassRole" in actions
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
    assert passed["Condition"]["StringEquals"]["iam:PassedToService"] == "ec2.amazonaws.com"


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
        assert root in workflow or (root == "terraform" and "terraform terraform/backup" in workflow)
    assert "terraform/state" in workflow
    assert "terraform/iam-bot" in workflow
    assert "init -backend=false" in workflow
    iam = (REPO / "terraform" / "iam-bot" / "main.tf").read_text(encoding="utf-8")
    assert "docs/grok-bot-dutch-blitz-policy.json" in iam
    assert 'resource "aws_iam_access_key"' not in iam
