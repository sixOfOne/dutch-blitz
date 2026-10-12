output "mode" {
  description = "local or aws depending on enable_aws."
  value       = var.enable_aws ? "aws" : "local"
}

output "role_name" {
  description = "Instance role the bot may pass to EC2 and may not edit."
  value       = var.enable_aws ? aws_iam_role.game[0].name : null
}

output "instance_profile_name" {
  description = "Profile name the game-night stack passes to RunInstances."
  value       = var.enable_aws ? aws_iam_instance_profile.game[0].name : null
}

output "role_note" {
  description = "Why down does not delete this role or the idle topic."
  value       = "This state is separate from terraform/. down does not destroy it. The bot can pass the role to EC2 and cannot change its policy. The SNS topic and its email subscription stay here so the confirmation survives the next night."
}

output "idle_topic_arn" {
  description = "Topic the instance role may publish to. Null when enable_aws is false."
  value       = var.enable_aws ? aws_sns_topic.idle[0].arn : null
}

output "idle_topic_name" {
  description = "Name the host turns into an ARN with the region and the account id from IMDS. Default dutch-blitz-idle."
  value       = var.enable_aws ? aws_sns_topic.idle[0].name : null
}

output "idle_alert_note" {
  description = "The subscription does not deliver until the owner confirms it."
  value       = "Confirm the SNS subscription email once. down does not delete this topic. The address is stored in this state file, which the bot cannot read."
}
