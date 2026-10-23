variable "enable_aws" {
  description = "When true, create or reuse the GitHub OIDC provider and the deployer role. Keep false for a local validate that calls nothing."
  type        = bool
  default     = false
}

variable "aws_region" {
  description = "Region for the provider. The game night runs in us-east-2. The trust policy is global."
  type        = string
  default     = "us-east-2"
}

variable "github_oidc_provider_arn" {
  description = "ARN of an existing IAM OIDC provider for token.actions.githubusercontent.com. Leave empty and apply creates that provider. One URL can have only one provider in the account."
  type        = string
  default     = ""

  validation {
    condition = (
      var.github_oidc_provider_arn == "" ||
      can(regex(
        "^arn:aws:iam::[0-9]{12}:oidc-provider/token\\.actions\\.githubusercontent\\.com$",
        var.github_oidc_provider_arn,
      ))
    )
    error_message = "github_oidc_provider_arn must be empty, or the ARN of the token.actions.githubusercontent.com provider."
  }
}

variable "bot_user_name" {
  description = "IAM user terraform/iam-bot created. This root looks up <name>-compute and <name>-records and attaches those policies. It does not create them."
  type        = string
  default     = "grok-bot-dutch-blitz"

  validation {
    condition     = can(regex("^[A-Za-z0-9+=,.@_-]{1,64}$", var.bot_user_name))
    error_message = "bot_user_name must be a single IAM user name."
  }
}
