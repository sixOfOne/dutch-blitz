# Bootstrap bucket for Terraform state.
#
# The game-night stack and the backup stack used to keep terraform.tfstate
# on one Mac. A second machine cannot apply or destroy what it cannot
# read, and two copies of that file would drift. This root creates one
# S3 bucket that both machines use.
#
# Apply it once, yourself, before the other roots are migrated.
# The s3 backend below cannot store this first apply: the bucket does
# not exist yet. init -backend=false does not help. apply then stops
# with "Backend initialization required", because that flag skips
# backend initialization while the s3 block is still in this file.
# Create a gitignored terraform/state/bootstrap_override.tf containing
#   terraform { backend "local" {} }
# then init and apply, delete the override, and
# init -migrate-state -backend-config=../backend.hcl.
# The steps are in docs/remote-state.md.
#
# prevent_destroy is a literal, same as the backup bucket. Terraform
# will not delete this bucket until that line is removed. The bot
# policy also has no s3:DeleteBucket.
terraform {
  # 1.10 added S3 lock files (use_lockfile). CI runs 1.16.5.
  # DynamoDB locking is the older mechanism and is not used.
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.68"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }

  # Partial backend. bucket and region come from terraform/backend.hcl,
  # which is gitignored, via -backend-config. A backend block is parsed
  # before variables exist, so it cannot say bucket = var.state_bucket_name.
  # The key is here so this root's state object is not the game-night one.
  # use_lockfile writes dutch-blitz/state/terraform.tfstate.tflock beside
  # the state object. Whichever machine holds that object proceeds; the
  # other waits or stops. encrypt = true sends the AES256 header, which
  # matches the bucket default encryption below.
  backend "s3" {
    key          = "dutch-blitz/state/terraform.tfstate"
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
    note = "AWS is off. The state bucket is not created."
  }
}

resource "null_resource" "state_checks" {
  lifecycle {
    precondition {
      condition     = !var.enable_aws || can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.state_bucket_name))
      error_message = "enable_aws requires a lowercase state_bucket_name of at least 3 characters."
    }

    precondition {
      condition     = !var.enable_aws || var.state_bucket_name != "dutch-blitz-tfstate-replace-me"
      error_message = "Replace the example state_bucket_name in terraform/state/terraform.tfvars. Do not apply the placeholder."
    }
  }
}

resource "aws_s3_bucket" "state" {
  count         = var.enable_aws ? 1 : 0
  bucket        = var.state_bucket_name
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name    = var.state_bucket_name
    Project = "dutch-blitz"
  }
}

resource "aws_s3_bucket_versioning" "state" {
  count  = var.enable_aws ? 1 : 0
  bucket = aws_s3_bucket.state[0].id

  # A bad apply overwrites the state object. Versioning keeps the
  # previous bytes so the bucket can be restored without the local file.
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  count  = var.enable_aws ? 1 : 0
  bucket = aws_s3_bucket.state[0].id

  # SSE-S3 (AES256) rather than a customer KMS key. A KMS key would add
  # a key policy and kms:Decrypt on every state read, including the bot.
  # Default encryption still applies when a client omits the header.
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  count  = var.enable_aws ? 1 : 0
  bucket = aws_s3_bucket.state[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  count  = var.enable_aws ? 1 : 0
  bucket = aws_s3_bucket.state[0].id

  # BucketOwnerEnforced turns ACLs off. The public-access block above
  # already rejects public ACLs; this stops a private ACL from being
  # the thing that grants a read.
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

data "aws_iam_policy_document" "state_bucket" {
  count = var.enable_aws ? 1 : 0

  # Deny is not a public grant, so the public-access block allows this
  # statement. It applies to every principal, including the owner and
  # the bot. IAM still decides who may call the bucket at all.
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.state[0].arn,
      "${aws_s3_bucket.state[0].arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  count  = var.enable_aws ? 1 : 0
  bucket = aws_s3_bucket.state[0].id
  policy = data.aws_iam_policy_document.state_bucket[0].json

  depends_on = [aws_s3_bucket_public_access_block.state]
}
