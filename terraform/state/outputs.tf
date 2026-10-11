output "mode" {
  description = "local or aws depending on enable_aws."
  value       = var.enable_aws ? "aws" : "local"
}

output "state_bucket" {
  description = "Bucket that stores Terraform state for every root."
  value       = var.enable_aws ? aws_s3_bucket.state[0].bucket : null
}

output "state_bucket_arn" {
  description = "ARN of the state bucket. The bot policy allows object access only under this bucket, and not a delete of the bucket itself."
  value       = var.enable_aws ? aws_s3_bucket.state[0].arn : null
}

output "backend_note" {
  description = "How the other roots find this bucket."
  value       = "Copy the bucket name into terraform/backend.hcl. Backend blocks cannot use variables, so that gitignored file is the partial backend configuration."
}
