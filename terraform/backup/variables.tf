# Same variables as terraform/variables.tf so one terraform.tfvars file
# can be passed to both stacks. This stack only reads enable_aws, aws_region,
# and backup_bucket_name.

variable "enable_aws" {
  description = "When true, provision the durable backup bucket. Keep false for a local plan that creates nothing."
  type        = bool
  default     = false
}

variable "aws_region" {
  description = "AWS region for the backup bucket. Must match the game-night stack."
  type        = string
  default     = "us-east-2"
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "name_prefix" {
  description = "Name prefix. Declared so the shared tfvars file applies."
  type        = string
  default     = "dutch-blitz"
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "instance_type" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = string
  default     = "t3.small"
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "root_volume_gb" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = number
  default     = 8
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "key_name" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = string
  default     = "neo-atari"
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "ssh_ingress_cidr" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = string
  default     = ""
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "ssh_ingress_cidrs" {
  description = "Declared so the shared tfvars file applies. Unused by this stack. The game-night stack opens SSH to these CIDRs."
  type        = list(string)
  default     = []
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "extra_ssh_public_keys" {
  description = "Declared so the shared tfvars file applies. Unused by this stack. The game-night stack installs these keys for ec2-user."
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
  description = "Globally unique S3 bucket for SQLite snapshots. This stack creates it and refuses to destroy it."
  type        = string
  default     = ""
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "domain" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = string
  default     = ""
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "acme_email" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = string
  default     = ""
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "dns_zone_name" {
  description = "Declared so the shared tfvars file applies. Unused by this stack. The game-night stack owns the A record."
  type        = string
  default     = ""
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "dns_record_name" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = string
  default     = "blitz"
}

# Declared so the shared terraform.tfvars file applies. This stack does
# not create the topic and does not read the thresholds. The game-night
# CLI copies the numbers into Ansible. terraform/game-role owns the topic.
# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "alert_email" {
  description = "Declared so the shared tfvars file applies. Unused by this stack. terraform/game-role subscribes this address. Do not commit the real address."
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
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = number
  default     = 60

  validation {
    condition     = var.idle_warn_minutes >= 1 && var.idle_warn_minutes < var.idle_shutdown_minutes
    error_message = "idle_warn_minutes must be at least 1 and less than idle_shutdown_minutes."
  }
}

variable "idle_shutdown_minutes" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = number
  default     = 90

  validation {
    condition     = var.idle_shutdown_minutes >= 2
    error_message = "idle_shutdown_minutes must be at least 2."
  }
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "uptime_warn_minutes" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = number
  default     = 420

  validation {
    condition     = var.uptime_warn_minutes >= 1 && var.uptime_warn_minutes < var.uptime_shutdown_minutes
    error_message = "uptime_warn_minutes must be at least 1 and less than uptime_shutdown_minutes."
  }
}

variable "uptime_shutdown_minutes" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = number
  default     = 480

  validation {
    condition     = var.uptime_shutdown_minutes >= 2
    error_message = "uptime_shutdown_minutes must be at least 2."
  }
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "idle_snooze_minutes" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = number
  default     = 120

  validation {
    condition     = var.idle_snooze_minutes >= 1 && var.idle_snooze_minutes <= 1440
    error_message = "idle_snooze_minutes must be from 1 to 1440."
  }
}

# tflint-ignore: terraform_unused_declarations  # shared tfvars; this root only validates the value
variable "idle_check_minutes" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = number
  default     = 15

  validation {
    condition     = var.idle_check_minutes >= 1 && var.idle_check_minutes <= 60
    error_message = "idle_check_minutes must be from 1 to 60."
  }
}
