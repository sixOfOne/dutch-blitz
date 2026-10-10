output "mode" {
  description = "local or aws depending on enable_aws."
  value       = var.enable_aws ? "aws" : "local"
}

output "backup_bucket" {
  description = "Durable bucket name. Empty when AWS is off."
  value       = var.enable_aws ? aws_s3_bucket.backups[0].bucket : null
}

output "backup_note" {
  description = "How this bucket survives a game-night teardown."
  value       = "This state is separate from terraform/. down does not destroy it. prevent_destroy blocks an accidental destroy until that lifecycle line is removed."
}
