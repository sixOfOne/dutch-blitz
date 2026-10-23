output "mode" {
  description = "local or aws depending on enable_aws."
  value       = var.enable_aws ? "aws" : "local"
}

output "role_arn" {
  description = "Put this in the game-night environment variable AWS_ROLE_ARN. It is a variable, not a secret."
  value       = var.enable_aws ? aws_iam_role.deployer[0].arn : null
}

output "oidc_provider_arn" {
  description = "GitHub OIDC provider the deployer role trusts."
  value       = var.enable_aws ? local.oidc_provider_arn : null
}
