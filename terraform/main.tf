# Game-night instance. Destroying this stack does not delete the S3 backup
# bucket (terraform/backup) or the instance role (terraform/game-role).
# Those two stay up across nights. This stack only looks the instance
# profile up by name and passes it to RunInstances.
# State lives in the bucket from terraform/state, not in a local file.
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

  # Partial backend. bucket and region come from terraform/backend.hcl
  # (gitignored) via -backend-config. They cannot be variables: Terraform
  # reads this block before variable values exist. The key is local to
  # this root so an apply here cannot overwrite the backup bucket's state.
  # use_lockfile is the lock. DynamoDB is not used. encrypt matches the
  # state bucket's AES256 default. Migration: docs/remote-state.md.
  backend "s3" {
    key          = "dutch-blitz/game-night/terraform.tfstate"
    use_lockfile = true
    encrypt      = true
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
  manual_domain = trimsuffix(trimspace(var.domain), ".")
  dns_zone      = trimsuffix(lower(trimspace(var.dns_zone_name)), ".")
  dns_label     = trimsuffix(lower(trimspace(var.dns_record_name)), ".")
  dns_fqdn      = local.dns_zone != "" ? "${local.dns_label}.${local.dns_zone}" : ""
  # Route 53 wins when a zone is set. Otherwise keep today's domain or sslip.io.
  public_hostname     = local.dns_fqdn != "" ? local.dns_fqdn : local.manual_domain
  reserved_dns_labels = ["clerk", "accounts", "clkmail"]
  manage_dns          = var.enable_aws && local.dns_zone != ""
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
      condition = alltrue([
        for cidr in var.ssh_ingress_cidrs : can(cidrhost(cidr, 0))
      ])
      error_message = "Every entry in ssh_ingress_cidrs must be a CIDR, such as a public IP with /32."
    }

    precondition {
      condition = (
        local.manual_domain == "" ||
        (
          !strcontains(local.manual_domain, "://") &&
          !strcontains(local.manual_domain, "/") &&
          !strcontains(local.manual_domain, " ")
        )
      )
      error_message = "domain must be a hostname such as scores.example.com, not a URL."
    }

    precondition {
      condition = (
        local.dns_zone == "" ||
        (
          can(regex("^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,}$", local.dns_zone)) &&
          can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", local.dns_label)) &&
          !contains(local.reserved_dns_labels, local.dns_label)
        )
      )
      error_message = "dns_zone_name must be a domain such as sixofonedev.com, and dns_record_name a single label such as blitz. clerk, accounts, and clkmail are reserved."
    }

    precondition {
      condition     = local.dns_fqdn == "" || local.manual_domain == "" || lower(local.manual_domain) == local.dns_fqdn
      error_message = "When dns_zone_name is set, leave domain empty or set it to the same hostname."
    }
  }
}

data "aws_ami" "amazon_linux" {
  count       = var.enable_aws ? 1 : 0
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-kernel-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
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
  cidr_blocks       = concat([var.ssh_ingress_cidr], var.ssh_ingress_cidrs)
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

# The profile is created by terraform/game-role, which only the owner
# applies. This data source calls iam:GetInstanceProfile. It does not
# create, edit, or delete the role. up fails here if that root has not
# been applied yet, which is the point: the bot cannot invent a role.
data "aws_iam_instance_profile" "game" {
  count = var.enable_aws ? 1 : 0
  name  = "${var.name_prefix}-game"
}

resource "aws_instance" "game" {
  count                       = var.enable_aws ? 1 : 0
  ami                         = data.aws_ami.amazon_linux[0].id
  instance_type               = var.instance_type
  key_name                    = var.key_name
  vpc_security_group_ids      = [aws_security_group.game[0].id]
  iam_instance_profile        = data.aws_iam_instance_profile.game[0].name
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

# The zone already exists (Clerk CNAMEs and NS/SOA were added by hand).
# This data source only reads it. The one A record below is the only Route 53
# change, and it is destroyed with the instance. Do not import the zone or
# the clerk, accounts, clkmail, clk._domainkey, or clk2._domainkey records.
data "aws_route53_zone" "game" {
  count        = local.manage_dns ? 1 : 0
  name         = "${local.dns_zone}."
  private_zone = false
}

# These three used to be resources in this root, so a night that is up
# right now still has them in this state. removed tells the next apply
# to forget them without deleting them in AWS. down destroys everything
# still in state; without destroy = false it would delete the role on
# the way out and the instance would lose S3. After this apply, the
# addresses are gone from state. Leave the blocks here: a removed block
# that matches nothing is a no-op, and deleting them early makes an old
# state destroy the role. Adoption into terraform/game-role is
# docs/remote-state.md.
# from is the resource, not the [0] instance. count makes one instance,
# and a removed block cannot name the instance key. All instances of
# that resource leave state. destroy = false keeps the AWS object.
removed {
  from = aws_iam_role.game

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_iam_role_policy.backup_bucket

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_iam_instance_profile.game

  lifecycle {
    destroy = false
  }
}

resource "aws_route53_record" "game" {
  count   = local.manage_dns ? 1 : 0
  zone_id = data.aws_route53_zone.game[0].zone_id
  name    = local.dns_fqdn
  type    = "A"
  ttl     = 60
  records = [aws_instance.game[0].public_ip]
}
