output "mode" {
  description = "local or aws depending on enable_aws."
  value       = var.enable_aws ? "aws" : "local"
}

output "instance_id" {
  description = "EC2 instance id when AWS is enabled."
  value       = try(aws_instance.game[0].id, null)
}

output "public_ip" {
  description = "Public IPv4 of the game-night host."
  value       = try(aws_instance.game[0].public_ip, null)
}

output "ssh_host" {
  description = "SSH target for the game-night host."
  value       = try(aws_instance.game[0].public_ip, null)
}

output "hostname" {
  description = "Public hostname. Route 53 FQDN when dns_zone_name is set, otherwise domain, otherwise <ipv4>.sslip.io."
  value = var.enable_aws ? (
    local.public_hostname != "" ? local.public_hostname : "${aws_instance.game[0].public_ip}.sslip.io"
  ) : null
}

output "public_url" {
  description = "https origin to share. Stable across nights when dns_zone_name is set."
  value = var.enable_aws ? (
    "https://${local.public_hostname != "" ? local.public_hostname : "${aws_instance.game[0].public_ip}.sslip.io"}"
  ) : null
}

output "dns_fqdn" {
  description = "Route 53 name for this night. Null when the sslip.io hostname is in use."
  value       = var.enable_aws && local.dns_fqdn != "" ? local.dns_fqdn : null
}

output "backup_bucket" {
  description = "S3 bucket name the instance may read and write."
  value       = var.backup_bucket_name != "" ? var.backup_bucket_name : null
}

output "backup_note" {
  description = "Reminds operators that destroying this stack keeps the backup bucket."
  value       = "terraform destroy in this directory deletes the instance only. The backup bucket is managed by terraform/backup."
}
