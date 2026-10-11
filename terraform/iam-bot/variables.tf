variable "enable_aws" {
  description = "When true, create the bot user, attach its policy, and create the budget. Keep false for a local validate that calls nothing."
  type        = bool
  default     = false
}

variable "aws_region" {
  description = "Region substituted into the bot policy's EC2 statements. The game night runs in us-east-2."
  type        = string
  default     = "us-east-2"
}

variable "aws_account_id" {
  description = "12-digit account id. Set it in the gitignored tfvars file. The example value is rejected on apply."
  type        = string
  default     = "000000000000"
}

variable "state_bucket_name" {
  description = "Name of the bucket created by terraform/state. The bot may read and write only the game-night and backup state keys."
  type        = string
  default     = "dutch-blitz-tfstate-replace-me"
}

variable "backup_bucket_name" {
  description = "Name of the score-snapshot bucket created by terraform/backup."
  type        = string
  default     = "dutch-blitz-scores-replace-me"
}

variable "dns_zone_id" {
  description = "Existing public hosted zone id, such as Z0123456789EXAMPLE. Route 53 changes are limited to this zone. Set the real id only in the gitignored tfvars file."
  type        = string
  default     = "Z000000000000EXAMPLE"
}

variable "dns_record_name" {
  description = "Relative name the game-night stack publishes. The bot may change only <this-label>.* A records. Default matches terraform/variables.tf."
  type        = string
  default     = "blitz"
}

variable "name_prefix" {
  description = "Prefix of the instance role and instance profile in terraform/game-role. Both are named <prefix>-game. The game-night stack looks that profile up."
  type        = string
  default     = "dutch-blitz"
}

variable "key_name" {
  description = "Existing EC2 key pair the bot may use at launch. It cannot create or delete key pairs. Default matches terraform/variables.tf."
  type        = string
  default     = "neo-atari"
}

variable "bot_user_name" {
  description = "IAM user for the Linux assistant. Access keys are created outside Terraform so the secret is not stored in state."
  type        = string
  default     = "grok-bot-dutch-blitz"
}

variable "budget_name" {
  description = "Name of the account-wide monthly cost budget. The bot may view this budget and no other."
  type        = string
  default     = "dutch-blitz-monthly"
}

variable "monthly_budget_usd" {
  description = "Monthly account cost that triggers the alarm. A game night is about $0.10, so 10 is a forgotten-instance alarm. Raise it if this account pays for other work."
  type        = string
  default     = "10"
}

variable "budget_alert_email" {
  description = "Address that receives the budget email. Set it in the gitignored tfvars file. example.com is rejected on apply."
  type        = string
  default     = ""
  sensitive   = true
}

variable "budget_start" {
  description = "Start of the budget period. Budgets requires this format: YYYY-MM-DD_HH:MM."
  type        = string
  default     = "2026-10-01_00:00"
}
