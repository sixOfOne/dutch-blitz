# Remote Terraform state

The game-night stack (`terraform/`) and the backup bucket (`terraform/backup/`) used to store `terraform.tfstate` only on the Mac that ran them. `up` and `down` on a second machine would not see that state. They would try to create a second instance, or would refuse to destroy the one they could not find.

Both machines now share one S3 bucket. Terraform writes a lock object next to each state file (`use_lockfile`, Terraform 1.10 or newer). The Mac and the Linux assistant can each run `up` and `down`. They cannot do it at the same moment: the second command waits up to two minutes, then stops with the state unchanged.

DynamoDB is not used. It was the lock table for older Terraform. This repo's CI runs Terraform 1.16.5, and 1.10 is the first release with the S3 lock file, so a table would be a second thing to create and to grant.

## What is committed, and what is not

| File | In git? | Why |
| --- | --- | --- |
| `backend "s3"` block in each root | yes | The object key, `use_lockfile`, and `encrypt`. No account-specific names. |
| `terraform/backend.hcl.example` | yes | Shows the two settings the block cannot hold. |
| `terraform/backend.hcl` | no | The real state bucket name. A backend block cannot use variables, so this file is the partial configuration. |
| `terraform/state/terraform.tfvars` | no | The same bucket name, plus `enable_aws`, for the bootstrap apply. |
| `*.tfstate` | no | Already gitignored. Keep the old ones as backups after migration. |

The four keys, all in that one bucket:

| Root | Key |
| --- | --- |
| `terraform/state` | `dutch-blitz/state/terraform.tfstate` |
| `terraform/` | `dutch-blitz/game-night/terraform.tfstate` |
| `terraform/backup` | `dutch-blitz/backup/terraform.tfstate` |
| `terraform/iam-bot` | `dutch-blitz/iam-bot/terraform.tfstate` |

The bot user can read and write the game-night and backup keys, and their `.tflock` objects. It cannot read the other two. Those hold the bucket configuration and the budget email. Apply `terraform/state` and `terraform/iam-bot` with your own credentials.

## One-time migration

Do this on the Mac that already has the local state, with your own AWS credentials. Do not use the bot's access key for these steps. Nothing here runs `terraform apply` against the game-night or backup stack except the state bucket bootstrap.

Terraform 1.10 or newer is required (CI uses 1.16.5).

### 1. Copy the example files and edit them locally

```bash
cp terraform/state/terraform.tfvars.example terraform/state/terraform.tfvars
cp terraform/backend.hcl.example terraform/backend.hcl
```

Pick a bucket name that is free worldwide. Put that same name in both files. Leave the placeholder `dutch-blitz-tfstate-replace-me` out of the copies you apply. Both files are gitignored. Do not commit them.

### 2. Copy the existing local state somewhere outside the repo

Do this before any `init -migrate-state`. If a file is missing, skip that line.

```bash
mkdir -p "$HOME/dutch-blitz-tfstate-backup"
cp terraform/terraform.tfstate "$HOME/dutch-blitz-tfstate-backup/game-night.tfstate"
cp terraform/backup/terraform.tfstate "$HOME/dutch-blitz-tfstate-backup/backup.tfstate"
```

`terraform init -migrate-state` leaves the local file in place. Keep that file and the copy until step 6 looks right. Git already ignores `*.tfstate`, so the copies in the working tree are not committed. The copy under your home directory is the one that survives a later clean of the checkout.

### 3. Bootstrap the bucket with local state

The backend block is already in `terraform/state`. The first apply must ignore it, because the bucket it names does not exist yet. `-backend=false` does that. Do not delete the backend block.

```bash
terraform -chdir=terraform/state init -backend=false -input=false
terraform -chdir=terraform/state apply -var-file=terraform.tfvars
```

Read the plan. It creates one bucket, with versioning, AES256 encryption, a public-access block, ACL ownership locked to the bucket owner, and a bucket policy that denies any request that is not TLS. It does not create an instance. `prevent_destroy` is set, and `force_destroy` is false.

Then copy that new local state too, before you migrate it:

```bash
cp terraform/state/terraform.tfstate "$HOME/dutch-blitz-tfstate-backup/state.tfstate"
```

### 4. Migrate each root that already has a local state file

`backend.hcl` must name the bucket from step 3. From the repository root:

```bash
terraform -chdir=terraform/state init -migrate-state -backend-config=../backend.hcl
terraform -chdir=terraform init -migrate-state -backend-config=backend.hcl
terraform -chdir=terraform/backup init -migrate-state -backend-config=../backend.hcl
```

Terraform asks `Do you want to copy existing state to the new backend?` Type `yes` for each. `-input=false` would refuse to ask and stop; this step is meant to be typed by you.

`-chdir` makes the relative `-backend-config` path start in that root. `terraform/backend.hcl` is one directory up from `terraform/state` and from `terraform/backup`, and inside `terraform/` for the game-night root.

Do not pass `-reconfigure`. That points Terraform at the new bucket and leaves the old state behind, so the next apply thinks the resources are new and tries to create them again.

A root that has never been applied has no local state to copy. `terraform/iam-bot` is that case. Init it without `-migrate-state`, then apply it with your own credentials:

```bash
terraform -chdir=terraform/iam-bot init -backend-config=../backend.hcl
terraform -chdir=terraform/iam-bot apply -var-file=terraform.tfvars
```

That apply creates the bot user and the budget. It is documented in [bot-iam.md](bot-iam.md). The state it writes goes straight to S3.

### 5. Point the Linux assistant at the same backend

Copy `terraform/backend.hcl` and `terraform/terraform.tfvars` to the assistant's checkout by hand. Do not commit them. The bucket name is not an access key, and it still does not belong in the public repo. The tfvars file has your SSH CIDR.

The assistant's credentials are the bot access key, stored in its secret store. See the README. Its `up` and `down` pass `-backend-config` to `terraform init` for you.

### 6. Verify, then keep the local files

```bash
terraform -chdir=terraform state list
terraform -chdir=terraform/backup state list
terraform -chdir=terraform/state state list
```

The game-night list should include the instance, the security group and its rules, the instance role, and the `blitz` A record. It should not include the backup bucket. The backup list should include the bucket and its versioning, encryption, and public-access block. The state list should include the state bucket and its policy.

If a list is empty and the local file was not, stop. The local files from step 2 are the backup. Do not apply.

When the lists match what the Mac had, leave the local `terraform.tfstate` files where they are, or keep only the copies under `$HOME/dutch-blitz-tfstate-backup`. Either way they stay out of git. Do not delete the objects in S3.

## What the CLI does

`dutch_blitz_host up` and `down` call `terraform init -input=false -backend-config=<absolute path to terraform/backend.hcl>` before every AWS plan, apply, or destroy. Plan and apply also pass `-lock-timeout=120s`.

If `enable_aws` is false, the CLI inits with `-backend=false` instead, so the local no-op still needs no bucket and no credentials.

The CLI does not pass `-migrate-state`. After you pull this change, the first `up` on a machine that still has a local backend will stop and tell you to migrate. That is the step above, run once.

## What CI does

CI runs `terraform init -backend=false` and `terraform validate -var=enable_aws=false` in all four roots. `-backend=false` skips the S3 block, so the job never reads `backend.hcl` and never calls AWS. The workflow has no credentials.

## Two machines at once

Run `up` or `down` on one machine at a time. The lock object is `<key>.tflock`. Releasing it is an `s3:DeleteObject` on that object only. Versioning on the bucket keeps older state objects if a write goes wrong. The bot is not allowed `s3:DeleteObjectVersion` or `s3:DeleteBucket`.

`ssh_ingress_cidr` is still one CIDR. Add the other machine's public `/32` to `ssh_ingress_cidrs` in the gitignored `terraform/terraform.tfvars` so both can open SSH. HTTP and HTTPS stay open either way.
