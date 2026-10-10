variable "enable_aws" {
  description = "When true, create the state bucket. Keep false for a local validate that calls nothing."
  type        = bool
  default     = false
}

variable "aws_region" {
  description = "Region for the state bucket. The game-night host is us-east-2, and the bucket should be too."
  type        = string
  default     = "us-east-2"
}

variable "state_bucket_name" {
  description = "Globally unique name for the Terraform state bucket. Set it in the gitignored tfvars file. The same name goes in terraform/backend.hcl."
  type        = string
  default     = ""
}
