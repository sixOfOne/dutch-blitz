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
| `terraform/state/bootstrap_override.tf` | no | Temporary `backend "local" {}` so the first apply has a backend. `*_override.tf` is gitignored. Delete it before migrating. |
| `*.tfstate` | no | Already gitignored. Keep the old ones as backups after migration. |

The five keys, all in that one bucket:

| Root | Key |
| --- | --- |
| `terraform/state` | `dutch-blitz/state/terraform.tfstate` |
| `terraform/` | `dutch-blitz/game-night/terraform.tfstate` |
| `terraform/backup` | `dutch-blitz/backup/terraform.tfstate` |
| `terraform/iam-bot` | `dutch-blitz/iam-bot/terraform.tfstate` |
| `terraform/game-role` | `dutch-blitz/game-role/terraform.tfstate` |

The bot user can read and write the game-night and backup keys, and their `.tflock` objects. It cannot read the other three. Those hold the bucket configuration, the budget email, and the instance role's policy document. The game-role state also holds the idle-shutdown SNS subscription, which includes `alert_email`. Apply `terraform/state`, `terraform/iam-bot`, and `terraform/game-role` with your own credentials. `up` applies the backup root and the game-night root. It does not apply the role.

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

`terraform state list` cannot take this snapshot. Until a backend has been initialized, it stops with `Backend initialization required`. The copies are the before picture. Step 6 reads them with `terraform show`.

### 3. Bootstrap the bucket with a local backend override

The `backend "s3"` block is already in `terraform/state`. The bucket it names does not exist yet, so this first apply has to store state on disk. Leave that block in `main.tf`. `terraform init -backend=false` does not take its place: the flag skips backend initialization, and the following `apply` stops with `Backend initialization required`.

`*_override.tf` is in `.gitignore`. An override file replaces the backend for this one apply. Create it, initialize the local backend, and apply:

```bash
cat > terraform/state/bootstrap_override.tf <<'EOF'
terraform {
  backend "local" {}
}
EOF
terraform -chdir=terraform/state init -input=false
terraform -chdir=terraform/state apply -var-file=terraform.tfvars
```

Read the plan. It creates one bucket, with versioning, AES256 encryption, a public-access block, ACL ownership locked to the bucket owner, and a bucket policy that denies any request that is not TLS. It does not create an instance. `prevent_destroy` is set, and `force_destroy` is false.

Copy that new local state, then delete the override. The copy has to happen before the migration in the next step. Deleting the override puts `backend "s3"` back. Do not run `init -migrate-state` while `bootstrap_override.tf` is still there, or the backend stays local and nothing is copied to the bucket.

```bash
cp terraform/state/terraform.tfstate "$HOME/dutch-blitz-tfstate-backup/state.tfstate"
rm terraform/state/bootstrap_override.tf
```

`terraform state list` stops again as soon as the override is gone, until the next step initializes S3. The file you just copied is the record of this apply. `terraform state list -state=<file>` needs an initialized backend too, so it is not a way to read that copy early.

### 4. Migrate each root that already has a local state file

`bootstrap_override.tf` is already deleted. `backend.hcl` must name the bucket from step 3. From the repository root:

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

### 6. Verify against the saved state files

`terraform state list` fails with `Backend initialization required` until that root has been initialized. Do not run it before step 4 and expect a before picture. The copies under `$HOME/dutch-blitz-tfstate-backup` are the before picture.

`terraform show <file>` reads one of those copies. It uses the provider plugins already downloaded in that root, and it does not need the S3 backend, which is why it still works when `state list` does not. After step 4, `state list` is what S3 has. Compare the two. Do not run `init -backend=false` again here.

```bash
terraform -chdir=terraform show "$HOME/dutch-blitz-tfstate-backup/game-night.tfstate"
terraform -chdir=terraform/backup show "$HOME/dutch-blitz-tfstate-backup/backup.tfstate"
terraform -chdir=terraform/state show "$HOME/dutch-blitz-tfstate-backup/state.tfstate"

terraform -chdir=terraform state list
terraform -chdir=terraform/backup state list
terraform -chdir=terraform/state state list
```

The game-night list should match the resources in the saved `game-night.tfstate`. After the role move in the next section, it includes the instance, the security group and its rules, and the `blitz` A record. It does not include the backup bucket, and it does not include the instance role. The backup list should include the bucket and its versioning, encryption, and public-access block. The state list should include the state bucket and its policy.

If a list is empty and the saved file was not, stop. The files from steps 2 and 3 are the backup. Do not apply.

When the lists match what the Mac had, leave the local `terraform.tfstate` files where they are, or keep only the copies under `$HOME/dutch-blitz-tfstate-backup`. Either way they stay out of git. Do not delete the objects in S3.

### 7. Move the instance role into its own root

The role, its inline policy, and its instance profile used to be resources in `terraform/`. `down` destroys that whole state, so the role was created again on the next `up`. It was not persistent. That is why the bot policy used to include `iam:PutRolePolicy`. With `PassRole` and `RunInstances`, that write lets the bot replace the role's document, launch an instance, and read the instance credentials.

The role now lives in `terraform/game-role`. `up` only looks the profile up. A fresh create is the normal path, because a night that is down has already deleted the role.

Check the game-night state first. `terraform state list` works here only after the init in step 4. Before that init, the addresses are in the saved `game-night.tfstate` from step 2 (`terraform show` on that file).

```bash
terraform -chdir=terraform state list
```

If none of `aws_iam_role.game[0]`, `aws_iam_role_policy.backup_bucket[0]`, and `aws_iam_instance_profile.game[0]` are in that list, there is nothing to import. Create the role once, after the backup bucket exists (this root reads the bucket; it does not create it):

```bash
terraform -chdir=terraform/game-role init -backend-config=../backend.hcl
terraform -chdir=terraform/game-role apply -var-file=../terraform.tfvars
```

Do not pass `-migrate-state`. This root has no local state to copy. Init without that flag, then apply.

If those three addresses are in the game-night state, a night is up, or `down` has not run since the role lived in that root. Do not run `down` first. `down` destroys every address still in that state, so it would delete the role along with the instance. Apply instead, and this night stays up with the same profile.

Apply the game-night root. Do not destroy it. The `removed` blocks in `terraform/main.tf` name the resources without the `[0]` index (`aws_iam_role.game`, not `aws_iam_role.game[0]`). Terraform rejects an instance key in that block, and the block drops every instance of the resource. `destroy = false` keeps the role, the inline policy, and the profile in AWS. The plan should say they will no longer be managed. It should not say they will be destroyed. State list still prints the `[0]` addresses until this apply finishes.

```bash
terraform -chdir=terraform init -backend-config=backend.hcl
terraform -chdir=terraform apply -var-file=terraform.tfvars
```

Confirm the role is still in AWS. The name is `<name_prefix>-game`. The default prefix is `dutch-blitz`.

```bash
aws iam get-role --role-name dutch-blitz-game
```

If that succeeds, import the three objects into the new root. Import is a CLI command, not an `import` block, so CI does not need a real role. The inline policy id is `<role name>:<policy name>`, and the policy name is `<name_prefix>-backup`.

```bash
terraform -chdir=terraform/game-role init -backend-config=../backend.hcl
terraform -chdir=terraform/game-role import -var-file=../terraform.tfvars 'aws_iam_role.game[0]' dutch-blitz-game
terraform -chdir=terraform/game-role import -var-file=../terraform.tfvars 'aws_iam_role_policy.backup_bucket[0]' 'dutch-blitz-game:dutch-blitz-backup'
terraform -chdir=terraform/game-role import -var-file=../terraform.tfvars 'aws_iam_instance_profile.game[0]' dutch-blitz-game
terraform -chdir=terraform/game-role plan -var-file=../terraform.tfvars
```

The plan should be empty, or a tag difference you can read. Do not let it replace the role.

If `get-role` says the role does not exist, skip the import and run the apply from the empty-state case above. That creates a new role. The backup bucket must already exist.

`terraform state mv` between the two keys needs a state pull, a local move, and a state push. The push overwrites the remote object. The `removed` blocks plus import leave S3 as the source of truth. Use that path.

After this, `terraform state list` in `terraform/` does not include the role. `terraform state list` in `terraform/game-role` includes the role, the inline policy, and the profile. Later `down` destroys the instance, the security group, and the A record. It does not destroy this root.

A later apply of `terraform/game-role` adds the idle SNS topic, its email subscription, and a second inline policy (`<name_prefix>-idle-alerts`, only `sns:Publish` on that topic). Those are new addresses. There is nothing to import. The plan should add them and leave the existing role in place. The state file then contains `alert_email`. The bot still cannot read this key. Confirm the subscription email AWS sends, once.

Leave the `removed` blocks in `terraform/main.tf`. Once the addresses are gone they do nothing. Deleting the blocks while an old state still lists the role makes the next destroy delete the role.

If `terraform/iam-bot` was applied before this change, apply it again so the attached policies drop `iam:PutRolePolicy` and the instance-profile association actions. [bot-iam.md](bot-iam.md) has that step. The bot's first `up` after that looks the profile up. It fails at the data source if `terraform/game-role` has not been applied.

## What the CLI does

`dutch_blitz_host up` and `down` call `terraform init -input=false -backend-config=<absolute path to terraform/backend.hcl>` before every AWS plan, apply, or destroy. Plan and apply also pass `-lock-timeout=120s`.

If `enable_aws` is false, the CLI inits with `-backend=false` instead, so the local no-op still needs no bucket and no credentials.

The CLI does not pass `-migrate-state`. After you pull this change, the first `up` on a machine that still has a local backend will stop and tell you to migrate. That is the step above, run once.

## What CI does

CI runs `terraform init -backend=false` and `terraform validate -var=enable_aws=false` in all five roots (`terraform/`, `terraform/backup`, `terraform/state`, `terraform/iam-bot`, `terraform/game-role`). `-backend=false` skips the S3 block, so the job never reads `backend.hcl` and never calls AWS. The workflow has no credentials. That flag is for `validate`. It does not initialize a backend, so it is not the bootstrap in step 3. `apply`, `plan`, and `state list` after it stop with `Backend initialization required`.

## Two machines at once

Run `up` or `down` on one machine at a time. The lock object is `<key>.tflock`. Releasing it is an `s3:DeleteObject` on that object only. Versioning on the bucket keeps older state objects if a write goes wrong. The bot is not allowed `s3:DeleteObjectVersion` or `s3:DeleteBucket`.

`ssh_ingress_cidr` is still one CIDR. Add every public `/32` the other machine might use to `ssh_ingress_cidrs` in the gitignored `terraform/terraform.tfvars` so both can open SSH. HTTP and HTTPS stay open either way. Both operators keep the same `ssh_ingress_cidrs` and `extra_ssh_public_keys`. State is shared, so a different value on one machine rewrites the security group or the instance user data of the night the other is running.
