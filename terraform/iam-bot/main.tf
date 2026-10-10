# IAM user for the Linux assistant, and the account-wide monthly budget.
#
# Apply this root yourself. The policy it attaches does not include
# iam:CreateUser, iam:CreatePolicy, iam:Attach*, or budgets:ModifyBudget,
# so the bot cannot change its own rights or this alarm. Access keys are
# not resources here: an aws_iam_access_key would write the secret into
# this state file. Create the key afterwards and store it outside git.
#
# The policy text is docs/grok-bot-dutch-blitz-policy.json. This root
# only substitutes the placeholders. Edit the JSON, then apply again.
# An inline policy on a user is limited to 2,048 characters. A managed
# policy allows 6,144, and this document is larger than that too, so
# apply attaches it as two managed policies. The split is the quota,
# not two different privilege levels. The bot is still not granted
# iam:CreatePolicy or iam:AttachUserPolicy; your credentials do that.
terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }

  # Same partial backend as the other roots. This state contains the
  # alert email, so the bot policy does not grant GetObject on this key.
  # Only your credentials should read it. See docs/remote-state.md.
  backend "s3" {
    key          = "dutch-blitz/iam-bot/terraform.tfstate"
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
  role_name = "${var.name_prefix}-game"
  # Each replace fills one placeholder from the gitignored tfvars.
  # The JSON file in docs/ keeps the example values so the public repo
  # has no account id, bucket name, hosted zone id, or email.
  # us-east-2 is not an account secret; it is replaced so the EC2
  # condition follows aws_region instead of a second hardcoded copy.
  bot_policy_json = replace(
    replace(
      replace(
        replace(
          replace(
            replace(
              replace(
                replace(
                  replace(
                    file("${path.module}/../../docs/grok-bot-dutch-blitz-policy.json"),
                    "000000000000",
                    var.aws_account_id,
                  ),
                  "dutch-blitz-tfstate-replace-me",
                  var.state_bucket_name,
                ),
                "dutch-blitz-scores-replace-me",
                var.backup_bucket_name,
              ),
              "Z000000000000EXAMPLE",
              var.dns_zone_id,
            ),
            "dutch-blitz-game",
            local.role_name,
          ),
          "key-pair/neo-atari",
          "key-pair/${var.key_name}",
        ),
        "blitz.*",
        "${var.dns_record_name}.*",
      ),
      "dutch-blitz-monthly",
      var.budget_name,
    ),
    "us-east-2",
    var.aws_region,
  )
  bot_policy_document = jsondecode(local.bot_policy_json)
  # Statements through PassRole are the EC2 and IAM half. Everything
  # after that (Route 53, S3, cost, budget) is the other half. A new
  # statement in the JSON lands in the second policy unless its Sid is
  # added here. The preconditions below reject a half that outgrows
  # the managed-policy quota.
  compute_sids = toset([
    "DescribeEc2InUsEast2",
    "RunTaggedGameInstanceAndVolume",
    "RunInstancesOnSupportingResources",
    "CreateGameSecurityGroup",
    "DeleteTaggedGameInstanceAndSecurityGroup",
    "ManageRulesOnTaggedGameSecurityGroup",
    "TagGameNightResources",
    "AdjustInstanceAndAttachProfile",
    "ManageGameRole",
    "ManageGameInstanceProfile",
    "PassGameRoleToEc2Only",
  ])
  compute_statements = [
    for statement in local.bot_policy_document.Statement : statement
    if contains(local.compute_sids, statement.Sid)
  ]
  records_statements = [
    for statement in local.bot_policy_document.Statement : statement
    if !contains(local.compute_sids, statement.Sid)
  ]
  compute_policy_json = jsonencode({
    Version   = "2012-10-17"
    Statement = local.compute_statements
  })
  records_policy_json = jsonencode({
    Version   = "2012-10-17"
    Statement = local.records_statements
  })
}

resource "null_resource" "local_ready" {
  count = var.enable_aws ? 0 : 1

  triggers = {
    note = "AWS is off. The bot user and the budget are not created."
  }
}

resource "null_resource" "bot_checks" {
  lifecycle {
    precondition {
      condition     = can(jsondecode(local.bot_policy_json))
      error_message = "docs/grok-bot-dutch-blitz-policy.json is not valid JSON after placeholder substitution."
    }

    precondition {
      condition     = length(local.compute_statements) == length(local.compute_sids)
      error_message = "A Sid in compute_sids is missing from the bot policy JSON. The two managed policies would not match the document."
    }

    precondition {
      condition = (
        length(local.compute_policy_json) <= 6144 &&
        length(local.records_policy_json) <= 6144
      )
      error_message = "One half of the bot policy exceeds the 6,144 character managed-policy quota (compute ${length(local.compute_policy_json)}, records ${length(local.records_policy_json)}). Move a Sid to the other half or shorten the JSON."
    }

    precondition {
      condition = (
        strcontains(file("${path.module}/../../docs/grok-bot-dutch-blitz-policy.json"), "000000000000") &&
        strcontains(file("${path.module}/../../docs/grok-bot-dutch-blitz-policy.json"), "dutch-blitz-tfstate-replace-me") &&
        strcontains(file("${path.module}/../../docs/grok-bot-dutch-blitz-policy.json"), "dutch-blitz-scores-replace-me") &&
        strcontains(file("${path.module}/../../docs/grok-bot-dutch-blitz-policy.json"), "Z000000000000EXAMPLE") &&
        strcontains(file("${path.module}/../../docs/grok-bot-dutch-blitz-policy.json"), "dutch-blitz-game") &&
        strcontains(file("${path.module}/../../docs/grok-bot-dutch-blitz-policy.json"), "key-pair/neo-atari") &&
        strcontains(file("${path.module}/../../docs/grok-bot-dutch-blitz-policy.json"), "blitz.*") &&
        strcontains(file("${path.module}/../../docs/grok-bot-dutch-blitz-policy.json"), "dutch-blitz-monthly") &&
        strcontains(file("${path.module}/../../docs/grok-bot-dutch-blitz-policy.json"), "us-east-2")
      )
      error_message = "The bot policy JSON is missing a placeholder. Keep the example values in docs/grok-bot-dutch-blitz-policy.json; tfvars replace them at apply time."
    }

    precondition {
      condition = (
        !var.enable_aws ||
        (
          can(regex("^[0-9]{12}$", var.aws_account_id)) &&
          var.aws_account_id != "000000000000"
        )
      )
      error_message = "Set aws_account_id in terraform/iam-bot/terraform.tfvars to the 12-digit account id. Do not commit that file."
    }

    precondition {
      condition = (
        !var.enable_aws ||
        (
          can(regex("^Z[A-Z0-9]+$", var.dns_zone_id)) &&
          var.dns_zone_id != "Z000000000000EXAMPLE"
        )
      )
      error_message = "Set dns_zone_id in terraform/iam-bot/terraform.tfvars to the real hosted zone id. Do not commit that file."
    }

    precondition {
      condition = (
        !var.enable_aws ||
        (
          can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.state_bucket_name)) &&
          var.state_bucket_name != "dutch-blitz-tfstate-replace-me" &&
          can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.backup_bucket_name)) &&
          var.backup_bucket_name != "dutch-blitz-scores-replace-me"
        )
      )
      error_message = "Set state_bucket_name and backup_bucket_name to the real bucket names. Do not apply the replace-me placeholders."
    }

    precondition {
      condition = (
        !var.enable_aws ||
        (
          can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.budget_alert_email)) &&
          !endswith(lower(var.budget_alert_email), "@example.com")
        )
      )
      error_message = "Set budget_alert_email in terraform/iam-bot/terraform.tfvars to a real address. alerts@example.com is a placeholder and is rejected."
    }

    precondition {
      condition     = !var.enable_aws || can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}:[0-9]{2}$", var.budget_start))
      error_message = "budget_start must look like 2026-10-01_00:00."
    }

    precondition {
      condition     = can(regex("^[A-Za-z0-9+=,.@_-]{1,64}$", var.bot_user_name))
      error_message = "bot_user_name must be a single IAM user name."
    }
  }
}

resource "aws_iam_policy" "compute" {
  count       = var.enable_aws ? 1 : 0
  name        = "${var.bot_user_name}-compute"
  description = "EC2, the game role, and PassRole for dutch_blitz_host up and down."
  policy      = local.compute_policy_json

  tags = {
    Name    = "${var.bot_user_name}-compute"
    Project = "dutch-blitz"
  }
}

resource "aws_iam_policy" "records" {
  count       = var.enable_aws ? 1 : 0
  name        = "${var.bot_user_name}-records"
  description = "Route 53 blitz record, backup and state buckets, and read-only cost for dutch_blitz_host."
  policy      = local.records_policy_json

  tags = {
    Name    = "${var.bot_user_name}-records"
    Project = "dutch-blitz"
  }
}

resource "aws_iam_user" "bot" {
  count = var.enable_aws ? 1 : 0
  name  = var.bot_user_name

  tags = {
    Name    = var.bot_user_name
    Project = "dutch-blitz"
  }
}

resource "aws_iam_user_policy_attachment" "compute" {
  count      = var.enable_aws ? 1 : 0
  user       = aws_iam_user.bot[0].name
  policy_arn = aws_iam_policy.compute[0].arn
}

resource "aws_iam_user_policy_attachment" "records" {
  count      = var.enable_aws ? 1 : 0
  user       = aws_iam_user.bot[0].name
  policy_arn = aws_iam_policy.records[0].arn
}

resource "aws_budgets_budget" "monthly" {
  count        = var.enable_aws ? 1 : 0
  name         = var.budget_name
  budget_type  = "COST"
  limit_amount = var.monthly_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  # Account-wide on purpose. A forgotten instance in this account should
  # trip the alarm even if it is not tagged dutch-blitz. A night itself
  # is about $0.10; the default limit of $10 is the "something is still
  # running" line, not a forecast of one evening.
  time_period_start = var.budget_start

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}
