# Agent notes

FastAPI scorekeeper in `app/`, `dutch_blitz_host` CLI in `src/`, Terraform roots in `terraform/` (game night), `terraform/backup`, `terraform/state`, `terraform/game-role`, and `terraform/iam-bot`. Default branch is `main`. Python is 3.11.

## Before you finish

Run the same checks as CI, from the repo root. They must pass. CI has no AWS credentials and no Clerk keys. Do not `terraform plan` or `apply` here. `init` uses `-backend=false`.

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check app src tests
ruff format --check app src tests
mypy
pytest
terraform fmt -check -diff -recursive
tflint --init && tflint --recursive --config="$(pwd)/.tflint.hcl"
# then init -backend=false and validate -var=enable_aws=false in each root
pip install 'ansible-core==2.19.14' 'ansible-lint==26.9.0'
ansible-playbook ansible/playbook.yml --syntax-check -i ansible/inventory/syntax-check.yml
ansible-lint ansible/playbook.yml --offline
```

The exact root list and pins are in the README's Continuous integration section.

## Do not

- Modify or delete the Clerk Route 53 records: `clerk`, `accounts`, `clkmail`, `clk._domainkey`, `clk2._domainkey`. Do not change the zone's NS or SOA. `down` deletes only the `blitz` A record. The hosted zone stays.
- Let the bot user edit the `dutch-blitz-game` role. `terraform/game-role` and `terraform/iam-bot` are applied by the owner only. `up` does not apply them. Keep the bot policy least-privilege: no `iam:PutRolePolicy`, no access-key APIs, no admin, no `Action: "*"`.
- Commit secrets. `.env`, `terraform.tfvars`, `*.auto.tfvars`, `backend.hcl`, `*.pem`, and Terraform state stay gitignored. Real account ids, bucket names, zone ids, and the alert email stay out of the repo. Do not create an `aws_iam_access_key` resource (the secret would land in state).
- Put a personal email on a commit. Use `50211093+sixOfOne@users.noreply.github.com`.
- Leave infrastructure running. A night is one small instance for a few hours, then `down`. `down` deletes the instance, not the backup bucket, the state bucket, the instance role, or the hosted zone. `prevent_destroy` on the buckets stays until the owner removes it on purpose.

## CI

Pin every action to a full commit SHA and put the release tag in a comment on that line (`# v1.2.3`). Dependabot's `github-actions` entry reads that comment. Workflows keep `permissions: contents: read`.
