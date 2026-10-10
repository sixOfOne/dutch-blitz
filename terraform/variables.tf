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

variable "backup_bucket_name" {
  description = "Globally unique S3 bucket that stores SQLite snapshots. Created by terraform/backup and kept when the instance is destroyed."
  type        = string
  default     = ""
}

variable "domain" {
  description = "Optional hostname, such as scores.example.com. Leave empty to use <public-ipv4>.sslip.io."
  type        = string
  default     = ""
}

variable "acme_email" {
  description = "Optional email for Let's Encrypt expiry notices. Leave empty to omit it."
  type        = string
  default     = ""
}
