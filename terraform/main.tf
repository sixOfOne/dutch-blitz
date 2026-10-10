# Game-night instance. Destroying this stack does not delete the S3 backup
# bucket; that bucket lives in terraform/backup with its own state.
terraform {
  required_version = ">= 1.5.0"

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
}

provider "aws" {
  region = var.aws_region

  # When AWS is disabled, placeholder creds keep plan/apply local-only.
  access_key = var.enable_aws ? null : "local-only"
  secret_key = var.enable_aws ? null : "local-only"

  skip_credentials_validation = !var.enable_aws
  skip_requesting_account_id  = !var.enable_aws
  skip_metadata_api_check     = true
  skip_region_validation      = !var.enable_aws
}

locals {
  domain_name = trimsuffix(trimspace(var.domain), ".")
}

resource "null_resource" "local_ready" {
  count = var.enable_aws ? 0 : 1

  triggers = {
    note = "AWS is off. Copy terraform.tfvars.example, set enable_aws=true, then apply."
  }
}

# Checked on every plan, including the local path, so a night cannot start
# with a missing bucket name, an open-ended SSH default, or a URL in domain.
resource "null_resource" "game_night_checks" {
  lifecycle {
    precondition {
      condition = (
        !var.enable_aws ||
        (
          length(var.key_name) > 0 &&
          var.ssh_ingress_cidr != "" &&
          can(cidrhost(var.ssh_ingress_cidr, 0)) &&
          can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.backup_bucket_name))
        )
      )
      error_message = "enable_aws requires key_name, a CIDR in ssh_ingress_cidr, and a lowercase backup_bucket_name."
    }

    precondition {
      condition = (
        local.domain_name == "" ||
        (
          !strcontains(local.domain_name, "://") &&
          !strcontains(local.domain_name, "/") &&
          !strcontains(local.domain_name, " ")
        )
      )
      error_message = "domain must be a hostname such as scores.example.com, not a URL."
    }
  }
}

data "aws_ami" "amazon_linux" {
  count       = var.enable_aws ? 1 : 0
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_s3_bucket" "backups" {
  count  = var.enable_aws ? 1 : 0
  bucket = var.backup_bucket_name
}

resource "aws_security_group" "game" {
  count = var.enable_aws ? 1 : 0
  name  = "${var.name_prefix}-game"
  # AWS security group description is immutable. Changing it forces a new
  # group while the instance still holds this one and wedges apply.
  # Keep this string. Edit ingress only through the separate rules below.
  description = "Dutch Blitz game night: HTTP, HTTPS, and SSH"

  tags = {
    Name    = "${var.name_prefix}-game"
    Project = "dutch-blitz"
  }
}

resource "aws_security_group_rule" "http" {
  count             = var.enable_aws ? 1 : 0
  type              = "ingress"
  security_group_id = aws_security_group.game[0].id
  from_port         = 80
  to_port           = 80
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "HTTP"
}

resource "aws_security_group_rule" "https" {
  count             = var.enable_aws ? 1 : 0
  type              = "ingress"
  security_group_id = aws_security_group.game[0].id
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "HTTPS"
}

resource "aws_security_group_rule" "ssh" {
  count             = var.enable_aws ? 1 : 0
  type              = "ingress"
  security_group_id = aws_security_group.game[0].id
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = [var.ssh_ingress_cidr]
  description       = "SSH"
}

resource "aws_security_group_rule" "egress" {
  count             = var.enable_aws ? 1 : 0
  type              = "egress"
  security_group_id = aws_security_group.game[0].id
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "All outbound"
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

resource "aws_instance" "game" {
  count                       = var.enable_aws ? 1 : 0
  ami                         = data.aws_ami.amazon_linux[0].id
  instance_type               = var.instance_type
  key_name                    = var.key_name
  vpc_security_group_ids      = [aws_security_group.game[0].id]
  iam_instance_profile        = aws_iam_instance_profile.game[0].name
  associate_public_ip_address = true

  root_block_device {
    volume_size           = var.root_volume_gb
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Name    = "${var.name_prefix}-game"
    Project = "dutch-blitz"
  }
}
