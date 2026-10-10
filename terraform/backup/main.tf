# Durable SQLite backup bucket. Separate state from the game-night instance
# so `dutch_blitz_host down` can destroy the EC2 host without this bucket.
#
# prevent_destroy below is a literal. Terraform will not delete the bucket
# until that line is removed. After removing it, destroy only this directory:
#   terraform -chdir=terraform/backup destroy -var-file=../terraform.tfvars
# A non-empty bucket still refuses to delete (force_destroy is false).
# Empty it first if you mean to retire the score history.
#
# Init this root with the shared backend file first, or destroy will be
# looking at an old local state:
#   terraform -chdir=terraform/backup init -backend-config=../backend.hcl
terraform {
  # 1.10 added S3 lock files (use_lockfile). CI runs 1.16.5.
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

  # Partial backend, same file as the game-night root, different key.
  # down destroys the instance and must not be able to point this state
  # at the game-night key. use_lockfile serializes the Mac and the Linux
  # assistant. Migration: docs/remote-state.md.
  backend "s3" {
    key          = "dutch-blitz/backup/terraform.tfstate"
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
    note = "AWS is off. The backup bucket is not created."
  }
}

resource "null_resource" "backup_checks" {
  lifecycle {
    precondition {
      condition     = !var.enable_aws || can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.backup_bucket_name))
      error_message = "enable_aws requires a lowercase backup_bucket_name of at least 3 characters."
    }
  }
}

resource "aws_s3_bucket" "backups" {
  count         = var.enable_aws ? 1 : 0
  bucket        = var.backup_bucket_name
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name    = var.backup_bucket_name
    Project = "dutch-blitz"
  }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  count  = var.enable_aws ? 1 : 0
  bucket = aws_s3_bucket.backups[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  count  = var.enable_aws ? 1 : 0
  bucket = aws_s3_bucket.backups[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "backups" {
  count  = var.enable_aws ? 1 : 0
  bucket = aws_s3_bucket.backups[0].id

  versioning_configuration {
    status = "Enabled"
  }
}
