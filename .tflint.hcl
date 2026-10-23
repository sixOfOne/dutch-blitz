plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "aws" {
  enabled = true
  version = "0.49.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"

  # Deep checks call AWS. CI has no credentials, and validate already
  # runs with enable_aws = false. Leave this off.
  deep_check = false
}
