# Instance role for the game-night host. Only the owner applies this root.
#
# It used to live in terraform/ and was destroyed by every `down`, then
# created again by the next `up`. That meant the bot's `up` needed
# iam:PutRolePolicy, iam:CreateRole, and iam:PassRole together. With
# those three, the bot could replace the role's policy with a broader
# one, launch an instance, and read that instance's credentials.
#
# The role now outlives the instance. `up` only looks the profile up
# and passes this role to EC2. The bot policy has iam:PassRole on this
# role and iam:GetInstanceProfile on this profile, and no other iam
# action. `down` does not run in this directory, so the role stays, and
# so does the idle-shutdown SNS topic created below. The email
# confirmation survives the next night. The instance role may
# sns:Publish to that one topic and to nothing else.
#
# Why a separate root, and not terraform/backup: `up` applies the backup
# root every night, with the bot's credentials. Putting the role there
# would make the bot plan and apply it, and would store the role in a
# state object the bot can rewrite. The backup root stays the bucket.
# This root is applied by you, once the bucket exists, and its state
# object is not in the bot policy.
#
# Apply:
#   terraform -chdir=terraform/game-role init -backend-config=../backend.hcl
#   terraform -chdir=terraform/game-role apply -var-file=../terraform.tfvars
# If a night is up and the role already exists, import it first.
# docs/remote-state.md has that case.
terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.69"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }

  # Owner-only state. The bot can list the key name and cannot read it.
  backend "s3" {
    key          = "dutch-blitz/game-role/terraform.tfstate"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.aws_region

  access_key = var.enable_aws ? null : "local-only"
  secret_key = var.enable_aws ? null : "local-only"

  skip_credentials_validation = !var.enable_aws
  skip_requesting_account_id  = !var.enable_aws
  skip_metadata_api_check     = true
  skip_region_validation      = !var.enable_aws
}

resource "null_resource" "local_ready" {
  count = var.enable_aws ? 0 : 1

  triggers = {
    note = "AWS is off. The instance role is not created."
  }
}

resource "null_resource" "role_checks" {
  lifecycle {
    precondition {
      condition = (
        !var.enable_aws ||
        (
          length(var.name_prefix) > 0 &&
          can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.backup_bucket_name))
        )
      )
      error_message = "enable_aws requires name_prefix and a lowercase backup_bucket_name. The bucket must already exist; this stack only reads it."
    }

    precondition {
      condition = (
        !var.enable_aws ||
        (
          can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.alert_email)) &&
          !endswith(lower(var.alert_email), "@example.com")
        )
      )
      error_message = "Set alert_email in terraform.tfvars to a real address before applying this root. Confirm the SNS subscription email once. alerts@example.com is a placeholder. Do not commit the address."
    }
  }
}

# The backup stack owns the bucket. This read is how the inline policy
# learns the ARN without this root also trying to create the bucket.
data "aws_s3_bucket" "backups" {
  count  = var.enable_aws ? 1 : 0
  bucket = var.backup_bucket_name
}

data "aws_iam_policy_document" "assume_ec2" {
  count = var.enable_aws ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "backup_bucket" {
  count = var.enable_aws ? 1 : 0

  statement {
    sid       = "ListBackupBucket"
    actions   = ["s3:ListBucket"]
    resources = [data.aws_s3_bucket.backups[0].arn]
  }

  statement {
    sid       = "ReadWriteBackupObjects"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${data.aws_s3_bucket.backups[0].arn}/*"]
  }
}

resource "aws_iam_role" "game" {
  count              = var.enable_aws ? 1 : 0
  name               = "${var.name_prefix}-game"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2[0].json

  tags = {
    Name    = "${var.name_prefix}-game"
    Project = "dutch-blitz"
  }
}

resource "aws_iam_role_policy" "backup_bucket" {
  count  = var.enable_aws ? 1 : 0
  name   = "${var.name_prefix}-backup"
  role   = aws_iam_role.game[0].id
  policy = data.aws_iam_policy_document.backup_bucket[0].json
}

resource "aws_iam_instance_profile" "game" {
  count = var.enable_aws ? 1 : 0
  name  = "${var.name_prefix}-game"
  role  = aws_iam_role.game[0].name
}

# The topic outlives the instance. `down` destroys terraform/, not this
# root, so the subscription confirmation is still in place the next night.
# Email delivery starts only after the owner clicks the link AWS sends.
resource "aws_sns_topic" "idle" {
  count = var.enable_aws ? 1 : 0
  name  = "${var.name_prefix}-idle"

  tags = {
    Name    = "${var.name_prefix}-idle"
    Project = "dutch-blitz"
  }
}

# Account root on the topic lets your admin user manage the subscription.
# It does not let the bot subscribe: the bot's identity policy has no
# sns action at all. The instance role is named so Publish works even
# when the default topic policy's SourceOwner condition does not match
# an instance-role call. The role's own policy, below, is still only
# sns:Publish on this ARN. The instance cannot add a second subscriber.
data "aws_caller_identity" "current" {
  count = var.enable_aws ? 1 : 0
}

data "aws_iam_policy_document" "idle_topic" {
  count = var.enable_aws ? 1 : 0

  statement {
    sid = "AllowAccountToManageIdleTopic"
    # Topic policies only accept topic-scoped actions (the same set as the
    # AWS default policy). Unsubscribe, subscription attributes, and tag
    # actions are rejected with "action out of service scope".
    actions = [
      "sns:GetTopicAttributes",
      "sns:SetTopicAttributes",
      "sns:DeleteTopic",
      "sns:AddPermission",
      "sns:RemovePermission",
      "sns:Subscribe",
      "sns:ListSubscriptionsByTopic",
      "sns:Publish",
    ]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current[0].account_id}:root"]
    }
    resources = [aws_sns_topic.idle[0].arn]
  }

  statement {
    sid     = "AllowGameRolePublish"
    actions = ["sns:Publish"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.game[0].arn]
    }
    resources = [aws_sns_topic.idle[0].arn]
  }
}

resource "aws_sns_topic_policy" "idle" {
  count  = var.enable_aws ? 1 : 0
  arn    = aws_sns_topic.idle[0].arn
  policy = data.aws_iam_policy_document.idle_topic[0].json
}

resource "aws_sns_topic_subscription" "idle_email" {
  count     = var.enable_aws ? 1 : 0
  topic_arn = aws_sns_topic.idle[0].arn
  protocol  = "email"
  endpoint  = var.alert_email
}

data "aws_iam_policy_document" "publish_idle_topic" {
  count = var.enable_aws ? 1 : 0

  statement {
    sid       = "PublishIdleAlerts"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.idle[0].arn]
  }
}

resource "aws_iam_role_policy" "publish_idle_topic" {
  count  = var.enable_aws ? 1 : 0
  name   = "${var.name_prefix}-idle-alerts"
  role   = aws_iam_role.game[0].id
  policy = data.aws_iam_policy_document.publish_idle_topic[0].json
}
