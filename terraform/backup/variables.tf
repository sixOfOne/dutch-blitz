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

variable "name_prefix" {
  description = "Name prefix. Declared so the shared tfvars file applies."
  type        = string
  default     = "dutch-blitz"
}

variable "instance_type" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = string
  default     = "t3.small"
}

variable "root_volume_gb" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = number
  default     = 8
}

variable "key_name" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = string
  default     = "neo-atari"
}

variable "ssh_ingress_cidr" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = string
  default     = ""
}

variable "ssh_ingress_cidrs" {
  description = "Declared so the shared tfvars file applies. Unused by this stack. The game-night stack opens SSH to these CIDRs."
  type        = list(string)
  default     = []
}

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

variable "domain" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = string
  default     = ""
}

variable "acme_email" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = string
  default     = ""
}

variable "dns_zone_name" {
  description = "Declared so the shared tfvars file applies. Unused by this stack. The game-night stack owns the A record."
  type        = string
  default     = ""
}

variable "dns_record_name" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = string
  default     = "blitz"
}
