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
```

The exact root list and pins are in the README's Continuous integration section. The Image workflow is not a required check. It builds on pull requests and publishes on `main`.

## What runs where

The game-night host runs `ghcr.io/sixofone/dutch-blitz`. It does not copy a working tree over SSH, and it does not run the `latest` tag. `terraform/templates/cloud-init.sh.tftpl` is the boot script. Terraform renders it into user data. `configure` pipes the current render over SSH. Keep it under 16 KiB. Clerk keys are in that script and therefore in Terraform state. Do not print the script.

Caddy on the host terminates TLS for `blitz.sixofonedev.com` (or the sslip.io name) and proxies to `127.0.0.1:8000`. The container listens on `0.0.0.0:8000` inside its own network namespace. The Docker publish is loopback only. The container healthcheck is a TCP connect. An HTTP request writes the activity stamp and would reset idle shutdown.

Backup and idle shutdown stay on the host. They use the instance role, the AWS CLI, and `shutdown`. The host copies `dutch_blitz_host` out of the image. Do not put AWS credentials in the image. Port 22 and the sshd drop-in stay. SSM is a later change. Do not remove SSH in passing.

`app_image` is not in the shared `terraform.tfvars` file. It is in `terraform/app.auto.tfvars`, which only the game-night directory loads. A new Terraform variable that belongs in the shared file has to be declared in every root that loads that file.

## Do not

- Modify or delete the Clerk Route 53 records: `clerk`, `accounts`, `clkmail`, `clk._domainkey`, `clk2._domainkey`. Do not change the zone's NS or SOA. `down` deletes only the `blitz` A record. The hosted zone stays.
- Let the bot user edit the `dutch-blitz-game` role. `terraform/game-role` and `terraform/iam-bot` are applied by the owner only. `up` does not apply them. Keep the bot policy least-privilege: no `iam:PutRolePolicy`, no access-key APIs, no admin, no `Action: "*"`. Do not edit `docs/grok-bot-dutch-blitz-policy.json` unless the task says so.
- Grant `ec2:ModifyInstanceAttribute` for anything except `instanceInitiatedShutdownBehavior`. AWS provider v6 calls that action after `RunInstances` to set shutdown behavior to `terminate`, which the launch already requested. The allow is `instance/*` in the game region, `aws:ResourceTag/Project=dutch-blitz` (the same tag terminate uses), and `ec2:Attribute=instanceInitiatedShutdownBehavior`. The service authorization reference lists that condition key for this action. `ec2:AssociateIamInstanceProfile` stays absent. `down` still backs up a tainted or un-id'd instance, looked up by that tag, before destroy.
- Commit secrets. `.env`, `terraform.tfvars`, `*.auto.tfvars`, `backend.hcl`, `*.pem`, and Terraform state stay gitignored. Real account ids, bucket names, zone ids, and the alert email stay out of the repo. Do not create an `aws_iam_access_key` resource (the secret would land in state).
- Put a personal email on a commit. Use `50211093+sixOfOne@users.noreply.github.com`.
- Leave infrastructure running. A night is one small instance for a few hours, then `down`. `down` deletes the instance, not the backup bucket, the state bucket, the instance role, or the hosted zone. `prevent_destroy` on the buckets stays until the owner removes it on purpose.

## CI

Pin every action to a full commit SHA and put the release tag in a comment on that line (`# v1.2.3`). Dependabot's `github-actions` entry reads that comment. The CI workflow keeps `permissions: contents: read`. The image publish job is the one that adds `packages: write`, and `id-token: write` plus `attestations: write` for provenance.
