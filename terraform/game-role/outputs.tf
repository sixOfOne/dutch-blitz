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
  description = "Why down does not delete this role."
  value       = "This state is separate from terraform/. down does not destroy it. The bot can pass the role to EC2 and cannot change its policy."
}
