# Same variables as terraform/variables.tf so one terraform.tfvars file
# can be passed with -var-file=../terraform.tfvars. This stack reads
# enable_aws, aws_region, name_prefix, and backup_bucket_name.

variable "enable_aws" {
  description = "When true, create the instance role and profile. Keep false for a local plan that creates nothing."
  type        = bool
  default     = false
}

variable "aws_region" {
  description = "AWS region. IAM is global; this matches the game-night stack and the backup bucket."
  type        = string
  default     = "us-east-2"
}

variable "name_prefix" {
  description = "Prefix for the role and instance profile. Both are named <prefix>-game. The game-night stack looks up that profile name."
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
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = list(string)
  default     = []
}

variable "backup_bucket_name" {
  description = "Existing snapshot bucket from terraform/backup. The role may list that bucket and get or put objects in it."
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
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = string
  default     = ""
}

variable "dns_record_name" {
  description = "Declared so the shared tfvars file applies. Unused by this stack."
  type        = string
  default     = "blitz"
}
