# GitHub Actions deployer for the Game night button.
#
# Apply this root yourself, with your own credentials. The role it
# creates cannot edit this root: its state key is absent from the bot
# policy, and the policy has no iam write except GetInstanceProfile and
# PassRole to the game role. up does not apply this directory.
#
# The role attaches the two managed policies terraform/iam-bot already
# created from docs/grok-bot-dutch-blitz-policy.json. Apply iam-bot
# first. A later edit of that JSON is picked up by re-applying iam-bot.
# This role sees the new document because it is the same policy. Re-apply
# this root when the trust policy or the OIDC provider setting changes.
#
# This repository was created on 2026-10-10. GitHub sets
# use_immutable_subject, so the token sub is the id form below. The
# trust policy requires that exact string, plus aud, event_name,
# ref, and workflow_ref. All five are StringEquals. There is no
# wildcard and no pattern operator. A pull_request job is a different
# event_name, and a reusable workflow's job_workflow_ref is not a
# condition here: this workflow is not reusable, and requiring that
# claim would make AssumeRole fail.
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
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Owner-only state. The deployer role can list the key name and
  # cannot read the object. See docs/remote-state.md.
  backend "s3" {
    key          = "dutch-blitz/gha-deployer/terraform.tfstate"
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

locals {
  create_oidc = var.enable_aws && var.github_oidc_provider_arn == ""
  # try() is "" while the provider resource is absent (enable_aws false,
  # or an ARN was passed in). The role exists only when enable_aws is
  # true, and then this is either the created ARN or the variable.
  oidc_provider_arn = (
    var.github_oidc_provider_arn != ""
    ? var.github_oidc_provider_arn
    : try(aws_iam_openid_connect_provider.github[0].arn, "")
  )
  github_subject = "repo:sixOfOne@50211093/dutch-blitz@1413603165:environment:game-night"
  github_workflow_ref = (
    "sixOfOne/dutch-blitz/.github/workflows/game-night.yml@refs/heads/main"
  )
}

resource "null_resource" "local_ready" {
  count = var.enable_aws ? 0 : 1

  triggers = {
    note = "AWS is off. The GitHub OIDC deployer role is not created."
  }
}

resource "null_resource" "deployer_checks" {
  lifecycle {
    precondition {
      condition     = can(regex("^[A-Za-z0-9+=,.@_-]{1,64}$", var.bot_user_name))
      error_message = "bot_user_name must be a single IAM user name, the same one terraform/iam-bot applied."
    }

    precondition {
      condition = (
        var.github_oidc_provider_arn == "" ||
        can(regex(
          "^arn:aws:iam::[0-9]{12}:oidc-provider/token\\.actions\\.githubusercontent\\.com$",
          var.github_oidc_provider_arn,
        ))
      )
      error_message = "github_oidc_provider_arn must be empty, or the ARN of the token.actions.githubusercontent.com provider."
    }
  }
}

# Count is 0 during validate (enable_aws false), so CI does not connect
# to GitHub. Apply fetches the current leaf certificate. AWS no longer
# checks this thumbprint for token.actions.githubusercontent.com, and
# the provider resource still requires the argument.
data "tls_certificate" "github" {
  count = local.create_oidc ? 1 : 0
  url   = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  count           = local.create_oidc ? 1 : 0
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github[0].certificates[0].sha1_fingerprint]

  tags = {
    Name    = "dutch-blitz-github-actions"
    Project = "dutch-blitz"
  }
}

resource "aws_iam_role" "deployer" {
  count                = var.enable_aws ? 1 : 0
  name                 = "dutch-blitz-gha-deployer"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "sts:AssumeRoleWithWebIdentity"
        Principal = {
          Federated = local.oidc_provider_arn
        }
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud"          = "sts.amazonaws.com"
            "token.actions.githubusercontent.com:sub"          = local.github_subject
            "token.actions.githubusercontent.com:event_name"   = "workflow_dispatch"
            "token.actions.githubusercontent.com:ref"          = "refs/heads/main"
            "token.actions.githubusercontent.com:workflow_ref" = local.github_workflow_ref
          }
        }
      }
    ]
  })

  tags = {
    Name    = "dutch-blitz-gha-deployer"
    Project = "dutch-blitz"
  }
}

# Same documents as the bot user. Names match terraform/iam-bot.
data "aws_iam_policy" "compute" {
  count = var.enable_aws ? 1 : 0
  name  = "${var.bot_user_name}-compute"
}

data "aws_iam_policy" "records" {
  count = var.enable_aws ? 1 : 0
  name  = "${var.bot_user_name}-records"
}

resource "aws_iam_role_policy_attachment" "compute" {
  count      = var.enable_aws ? 1 : 0
  role       = aws_iam_role.deployer[0].name
  policy_arn = data.aws_iam_policy.compute[0].arn
}

resource "aws_iam_role_policy_attachment" "records" {
  count      = var.enable_aws ? 1 : 0
  role       = aws_iam_role.deployer[0].name
  policy_arn = data.aws_iam_policy.records[0].arn
}
