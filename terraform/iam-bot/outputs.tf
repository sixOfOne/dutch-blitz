output "mode" {
  description = "local or aws depending on enable_aws."
  value       = var.enable_aws ? "aws" : "local"
}

output "bot_user_name" {
  description = "IAM user that receives the access key. Create the key in the console or CLI after apply. Terraform does not create it."
  value       = var.enable_aws ? aws_iam_user.bot[0].name : null
}

output "bot_policy_arns" {
  description = "The two managed policies that together are docs/grok-bot-dutch-blitz-policy.json. Split only because one managed policy is limited to 6,144 characters."
  value = var.enable_aws ? [
    aws_iam_policy.compute[0].arn,
    aws_iam_policy.records[0].arn,
  ] : null
}

output "budget_name" {
  description = "Monthly cost budget. Confirm the subscription email or the alarm never sends."
  value       = var.enable_aws ? aws_budgets_budget.monthly[0].name : null
}

output "credentials_note" {
  description = "Where the access key goes."
  value       = "Create an access key for this user and store it in the assistant's secret store. Do not commit it and do not put it in a tfvars file. Rotation steps are in the README."
}
