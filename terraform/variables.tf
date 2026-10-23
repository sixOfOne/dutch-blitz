variable "enable_aws" {
  description = "When true, provision the game-night EC2 host. Keep false for a local plan that creates nothing."
  type        = bool
  default     = false
}

variable "aws_region" {
  description = "AWS region for the game-night host and the backup bucket."
  type        = string
  default     = "us-east-2"
}

variable "name_prefix" {
  description = "Name prefix for game-night AWS resources."
  type        = string
  default     = "dutch-blitz"
}

variable "instance_type" {
  description = "EC2 instance type. t3.small is enough for a few friends."
  type        = string
  default     = "t3.small"
}

variable "root_volume_gb" {
  description = "gp3 root volume size in GiB."
  type        = number
  default     = 8
}

variable "key_name" {
  description = "Existing EC2 key pair name in aws_region. The private key stays on the controller."
  type        = string
  default     = "neo-atari"
}

variable "ssh_ingress_cidr" {
  description = "CIDR allowed to SSH. Use your public IP as a /32. Required when enable_aws is true. HTTP and HTTPS stay open so phones can join."
  type        = string
  default     = ""
}

variable "ssh_ingress_cidrs" {
  description = "Extra SSH CIDRs, such as the Linux assistant's public IP as a /32. ssh_ingress_cidr is still required. The game-night security group allows the whole list. Both operators keep the same list: state is shared, and a different value rewrites the security group."
  type        = list(string)
  default     = []
}

variable "extra_ssh_public_keys" {
  description = "SSH public keys added to ec2-user at boot, besides the EC2 key pair. Empty omits the cloud-config part; the boot script is still user data. Both operators keep the same list in the shared tfvars."
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for key in var.extra_ssh_public_keys :
      can(regex("^(ssh-ed25519|ssh-rsa) ", key)) &&
      !strcontains(key, "\n") &&
      !strcontains(key, "\r")
    ])
    error_message = "Each extra_ssh_public_keys entry must be one ssh-ed25519 or ssh-rsa public key line."
  }
}

variable "backup_bucket_name" {
  description = "Globally unique S3 bucket that stores SQLite snapshots. Created by terraform/backup and kept when the instance is destroyed."
  type        = string
  default     = ""
}

variable "domain" {
  description = "Optional hostname, such as scores.example.com. Leave empty to use <public-ipv4>.sslip.io. Ignored when dns_zone_name is set, unless it is the same name."
  type        = string
  default     = ""
}

variable "dns_zone_name" {
  description = "Existing public Route 53 zone to publish the game-night A record in, such as sixofonedev.com. Leave empty to keep the sslip.io hostname and create no DNS records."
  type        = string
  default     = ""
}

variable "dns_record_name" {
  description = "Relative name in dns_zone_name. Default blitz becomes blitz.sixofonedev.com. The zone itself is not created or destroyed."
  type        = string
  default     = "blitz"
}

variable "acme_email" {
  description = "Optional email for Let's Encrypt expiry notices. Leave empty to omit it. Caddy still requests a certificate either way."
  type        = string
  default     = ""

  validation {
    condition = (
      trimspace(var.acme_email) == "" ||
      can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", trimspace(var.acme_email)))
    )
    error_message = "acme_email must be empty or one email address."
  }
}

variable "app_image" {
  description = "Scorekeeper image. Pin ghcr.io/sixofone/dutch-blitz to a sha-<commit> tag or a sha256 digest. Put the real pin in terraform/app.auto.tfvars, not in the shared tfvars file. latest is rejected. The default sha-0000000 is a placeholder and enable_aws refuses it."
  type        = string
  default     = "ghcr.io/sixofone/dutch-blitz:sha-0000000"

  validation {
    condition = (
      can(regex("^ghcr\\.io/sixofone/dutch-blitz:sha-[0-9a-f]{7,40}$", var.app_image)) ||
      can(regex("^ghcr\\.io/sixofone/dutch-blitz@sha256:[0-9a-f]{64}$", var.app_image))
    )
    error_message = "app_image must be ghcr.io/sixofone/dutch-blitz:sha-<commit> or ghcr.io/sixofone/dutch-blitz@sha256:<64 hex digits>. Do not use latest."
  }
}

variable "clerk_publishable_key" {
  description = "Clerk publishable key. The host CLI passes this from the environment as TF_VAR_clerk_publishable_key. Do not put it in a tfvars file."
  type        = string
  default     = ""
  sensitive   = true

  validation {
    condition     = !strcontains(var.clerk_publishable_key, "\n") && !strcontains(var.clerk_publishable_key, "\r")
    error_message = "clerk_publishable_key must be a single line."
  }
}

variable "clerk_secret_key" {
  description = "Clerk secret key. The host CLI passes this from the environment as TF_VAR_clerk_secret_key. Do not put it in a tfvars file. It is written into user_data and therefore into Terraform state."
  type        = string
  default     = ""
  sensitive   = true

  validation {
    condition     = !strcontains(var.clerk_secret_key, "\n") && !strcontains(var.clerk_secret_key, "\r")
    error_message = "clerk_secret_key must be a single line."
  }
}

# Idle shutdown. Cloud-init writes these into /etc/dutch-blitz/idle.env.
# This stack does not create the SNS topic. terraform/game-role does, so
# the subscription confirmation survives `down`. Defaults: warn at 60
# minutes idle or 7 hours up, shut down at 90 minutes idle or 8 hours up.
# The timer itself is every 15 minutes, so a limit can be that late.
variable "alert_email" {
  description = "Address that receives idle-shutdown mail. Set it only in gitignored terraform.tfvars. terraform/game-role subscribes it to the SNS topic. Empty is rejected when enable_aws is true. alerts@example.com is a placeholder and is rejected."
  type        = string
  default     = ""
  sensitive   = true

  validation {
    condition = (
      var.alert_email == "" ||
      (
        can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.alert_email)) &&
        !endswith(lower(var.alert_email), "@example.com")
      )
    )
    error_message = "alert_email must be empty or one real address. Do not commit it. alerts@example.com is a placeholder."
  }
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "idle_warn_minutes" {
  description = "Minutes without an HTTP request before the idle warning email. Must be less than idle_shutdown_minutes."
  type        = number
  default     = 60

  validation {
    condition     = var.idle_warn_minutes >= 1 && var.idle_warn_minutes < var.idle_shutdown_minutes
    error_message = "idle_warn_minutes must be at least 1 and less than idle_shutdown_minutes."
  }
}

variable "idle_shutdown_minutes" {
  description = "Minutes without an HTTP request before the host backs up and shuts down. Default 90."
  type        = number
  default     = 90

  validation {
    condition     = var.idle_shutdown_minutes >= 2
    error_message = "idle_shutdown_minutes must be at least 2."
  }
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "uptime_warn_minutes" {
  description = "Minutes since boot before the uptime warning, unless a snooze moved the deadline. Must be less than uptime_shutdown_minutes. Default 7 hours."
  type        = number
  default     = 420

  validation {
    condition     = var.uptime_warn_minutes >= 1 && var.uptime_warn_minutes < var.uptime_shutdown_minutes
    error_message = "uptime_warn_minutes must be at least 1 and less than uptime_shutdown_minutes."
  }
}

variable "uptime_shutdown_minutes" {
  description = "Minutes since boot before shutdown even if the site is in use, plus any snooze added on the host. Default 8 hours."
  type        = number
  default     = 480

  validation {
    condition     = var.uptime_shutdown_minutes >= 2
    error_message = "uptime_shutdown_minutes must be at least 2."
  }
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "idle_snooze_minutes" {
  description = "Minutes added to the uptime deadline by `sudo /usr/local/bin/dutch-blitz-idle snooze`. Each run adds this many. It does not reset the idle clock."
  type        = number
  default     = 120

  validation {
    condition     = var.idle_snooze_minutes >= 1 && var.idle_snooze_minutes <= 1440
    error_message = "idle_snooze_minutes must be from 1 to 1440."
  }
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "idle_check_minutes" {
  description = "How often the on-server timer looks at idle and uptime. Default 15. A limit can be this late."
  type        = number
  default     = 15

  validation {
    condition     = var.idle_check_minutes >= 1 && var.idle_check_minutes <= 60
    error_message = "idle_check_minutes must be from 1 to 60."
  }
}
