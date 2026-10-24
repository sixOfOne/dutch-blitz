# Dutch Blitz

[![CI](https://github.com/sixOfOne/dutch-blitz/actions/workflows/ci.yml/badge.svg)](https://github.com/sixOfOne/dutch-blitz/actions/workflows/ci.yml)

A small score keeper for Dutch Blitz. Open it on a phone at the table, enter each round, and the same games are still there on any other device that can reach this server.

- 2 to 8 players
- Traditional scoring: +1 for each card in your Dutch piles, −2 for each card left in your Blitz pile
- The target is 75 unless you set another one when you start the game
- Games, players, and rounds are stored in SQLite

## Run it

From the repository root, with Python 3.11 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app
```

Then open http://127.0.0.1:8000.

`python -m app` is the one command that starts the site after dependencies are installed. It reads the settings below. To have phones on the same network join in:

```bash
DUTCH_BLITZ_HOST=0.0.0.0 python -m app
```

For local development with auto-reload, watch only the application package so saving a round does not restart the process when the database file changes:

```bash
uvicorn app.main:app --reload --reload-dir app
```

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt -r requirements-host.txt
pytest
```

The tests cover negative round scores, running totals, the default target and a custom target, the 2-player and 8-player limits, ties, saved history, undo, editing the last round, sign-in enforcement, invite links, deck color and icon, the migration that adds those columns, and that an outsider cannot see a game. Token checks in tests are stubbed, so the suite does not call Clerk.

## Continuous integration

Every pull request, and every push to `main`, runs the [CI workflow](.github/workflows/ci.yml). A failure shows up on the pull request. The workflow has no Clerk keys and no AWS credentials. It does not run `terraform plan` or `terraform apply`, and it does not create resources.

Two checks run in parallel. The names are the job names, so a ruleset that already requires them does not need a new check:

- **Python tests** runs on Python 3.11, the version in the game-night image. pip caches the wheels from `requirements.txt`, `requirements-dev.txt`, and `requirements-host.txt` (boto3, for the controller's SSM client). It runs `ruff check`, `ruff format --check`, and `mypy` (configured in `pyproject.toml`), then the suite.
- **Terraform and Ansible** checks formatting with `terraform fmt -check`, then `tflint` (`.tflint.hcl`, no AWS calls), then `terraform init -backend=false` and `terraform validate` in `terraform/`, `terraform/backup/`, `terraform/state/`, `terraform/iam-bot/`, `terraform/game-role/`, and `terraform/gha-deployer/` with `enable_aws` false. `-backend=false` does not read `backend.hcl` and does not call the state bucket. The job name still says Ansible so the branch ruleset does not have to change. Ansible itself is gone. The host boots a container from cloud-init. Image builds are a separate workflow, [image.yml](.github/workflows/image.yml), and only run when the app, the Dockerfile, or the host package changed. That workflow is not a required check. The Game night workflow is also separate. It is `workflow_dispatch` only, and CI does not run it.

A newer push to the same branch cancels the run that is still going, so an outdated commit does not spend the rest of its minutes.

Run the same checks from the repository root. Terraform 1.10 or newer is required, because the S3 backend locks state with `use_lockfile` (CI uses 1.16.5). Each root commits `.terraform.lock.hcl` for linux_amd64, darwin_arm64, and darwin_amd64. `terraform init` installs the locked providers. Dependabot's terraform entry updates those lock files when a release is inside the version constraint.

```bash
pip install -r requirements.txt -r requirements-dev.txt -r requirements-host.txt
ruff check app src tests
ruff format --check app src tests
mypy
pytest

terraform fmt -check -diff -recursive
tflint --init
tflint --recursive --config="$(pwd)/.tflint.hcl"
terraform -chdir=terraform init -backend=false -input=false
terraform -chdir=terraform validate -var=enable_aws=false
terraform -chdir=terraform/backup init -backend=false -input=false
terraform -chdir=terraform/backup validate -var=enable_aws=false
terraform -chdir=terraform/state init -backend=false -input=false
terraform -chdir=terraform/state validate -var=enable_aws=false
terraform -chdir=terraform/iam-bot init -backend=false -input=false
terraform -chdir=terraform/iam-bot validate -var=enable_aws=false
terraform -chdir=terraform/game-role init -backend=false -input=false
terraform -chdir=terraform/game-role validate -var=enable_aws=false
terraform -chdir=terraform/gha-deployer init -backend=false -input=false
terraform -chdir=terraform/gha-deployer validate -var=enable_aws=false
```

## Repository security

The actions in the CI workflow and the image workflow are pinned to commit SHAs. [`.github/dependabot.yml`](.github/dependabot.yml) opens weekly pull requests for Python requirements, those action pins, the Terraform providers, and the Docker base image. How to report a vulnerability privately is in [`SECURITY.md`](SECURITY.md).

Branch protection, Dependabot alerts, secret scanning, and private vulnerability reporting are switches in the GitHub settings. They are not changed by a pull request. The clicks, and what each one protects against, are in [docs/repo-security.md](docs/repo-security.md).

## Configuration

Copy `.env.example` to `.env` if you want the variables loaded automatically. `python -m app` reads that file without overriding variables you already exported. `.env` and other `.env.*` files are gitignored. `.env.example` stays in the repo. Do not put real keys in the repository.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DUTCH_BLITZ_DB_PATH` | `data/dutch_blitz.sqlite` | SQLite file. The directory is created if needed. |
| `DUTCH_BLITZ_HOST` | `127.0.0.1` | Address the server binds to. |
| `DUTCH_BLITZ_PORT` | `8000` | Port the server listens on. |
| `CLERK_PUBLISHABLE_KEY` | unset | Clerk publishable key (`pk_test_...` or `pk_live_...`). Safe to send to the browser. |
| `CLERK_SECRET_KEY` | unset | Clerk secret key (`sk_test_...` or `sk_live_...`). Required on the server. Never sent to the browser. |
| `CLERK_FRONTEND_API` | decoded from the publishable key | Frontend API host. A production key for this domain decodes to `clerk.sixofonedev.com`. A development key decodes to a `*.clerk.accounts.dev` host. The app uses whichever host is in the key. |
| `CLERK_ISSUER` | `https://<frontend API>` | Expected `iss` on the session token. |
| `CLERK_JWKS_URL` | `<issuer>/.well-known/jwks.json` | Public keys used to verify the session JWT. |
| `CLERK_AUTHORIZED_PARTIES` | unset | Optional comma-separated origins. When set, the token `azp` claim must be one of them. |
| `CLERK_DEV_BYPASS` | off | Set to `1`, `true`, `yes`, or `on` only on a machine that is not shared. |
| `CLERK_DEV_USER_ID` | `dev-user` | The single account used while the bypass is on. |

The database file is gitignored. A game night on EC2 uses the same variables. `CLERK_DEV_BYPASS` stays off on that host.

## Sign-in

Each signed-in person sees the games they created and the games where they have claimed a seat. Anyone else gets the same not-found page as a missing game.

1. In the [Clerk dashboard](https://dashboard.clerk.com), create an application.
2. Under API keys, copy the publishable key into `CLERK_PUBLISHABLE_KEY` and the secret key into `CLERK_SECRET_KEY`.
3. Add `http://127.0.0.1:8000` as an allowed origin for that Clerk instance if the dashboard asks for one.
4. From the repository root, run `python -m app` and open http://127.0.0.1:8000.

The pages load Clerk's prebuilt sign-in and user button from the publishable key. The browser keeps the session token in a short-lived `__session` cookie and refreshes it before a same-origin form post or link. The server checks that token on every request except `/static/...`, using Clerk's public JWKS. The secret key stays on the server so a deploy cannot start half-configured; signature checks do not use it.

Until both keys are set, the sign-in page explains what is missing and game routes respond with 401.

### Development bypass

`CLERK_DEV_BYPASS` is off unless you set it. When it is on, the server does not load Clerk and treats every visitor as `CLERK_DEV_USER_ID` (or `dev-user`). A banner on each page says the bypass is on. Everyone who can open the server shares that one account, so turn the flag off before sharing the process or deploying.

### Games saved before accounts

Opening an older SQLite file adds any missing columns. Rows saved before accounts keep `owner_id` NULL. They stay in the database, they are not deleted, and no account can see, claim, or invite them. Games saved before sharing keep the invite turned off and every seat unclaimed. The owner of those games can still open them and can turn on an invite later.

## Sharing a seat

The owner shares a game with a link. This does not look people up by email, so it does not call Clerk's user API. The other person must already be able to sign in with Clerk.

1. On the game page, choose **Create invite link** and send that URL to the table.
2. Each person opens it while signed in and chooses **Play as** their name. One account can hold one seat.
3. The game then shows up in their list. They can read the whole score sheet and enter the next round.
4. **New invite link** invalidates the old URL and leaves current seats in place. **Turn invite link off** stops new people from joining and also leaves current seats in place. **Remove** on a seat drops that account; the seat is open again.

**This is me** lets the owner claim their own seat without using the link.

Anyone who has claimed a seat can enter a round. Only the owner can undo a round, fix the last round, create or change the invite, or remove someone. There is no separate delete-game action.

## The scorekeeper image

GitHub Actions builds `Dockerfile` and publishes `ghcr.io/sixofone/dutch-blitz`. A pull request only builds. A push to `main`, and a manual run of the Image workflow, push a `sha-<short>` tag. `main` and `latest` are pushed only from `main`. The game-night host does not run `main` or `latest`. `app_image` has to be a `sha-` tag or a `sha256` digest. The Game night workflow uses the `sha-` tag of `origin/main` when that manifest exists. A Terraform-only commit has no image, so the workflow then reads the revision on the `main` tag, and if that tag is not there yet it uses the newest successful Image run on `main` whose `sha-` manifest exists. It refuses to continue when that manifest is missing, so `up` does not launch an instance that cannot pull.

The workflow logs in with `GITHUB_TOKEN`. There is no personal access token. The Dockerfile sets `org.opencontainers.image.source` to this repository, which is how GitHub links the package to the repo when the workflow creates it. Linking by hand is only needed if the package page does not already show the repository.

GitHub's documentation disagrees with itself about the first publish. The Actions guide says a package created by `GITHUB_TOKEN` inherits the repository visibility, so this public repo should produce a public package and the instance can `docker pull` with no registry login. The Container registry guide still says a package is private until you change it, and that making it public cannot be undone. After the first push, open the package settings. If the visibility is already Public, leave it. If it is Private, change it to Public or the instance cannot pull it. Anonymous pulls work only for a public container package.

## Game night on EC2

A night is one `t3.small` in `us-east-2` (Amazon Linux 2023) for a few hours, then the instance is deleted. Scores are copied to an S3 bucket that stays. Three hours is about **$0.10**: the instance is about $0.021 an hour, and the disk and a small SQLite upload add a few cents. The bucket is pennies a month and is not part of that teardown.

From the repository root, with [Terraform](https://developer.hashicorp.com/terraform/install) 1.10 or newer and Python 3.11. The controller needs boto3 for SSM. The scorekeeper image does not install it. 1.10 is the first release that locks S3 state with `use_lockfile`. CI uses 1.16.5. `up`, `configure`, `backup`, and `down` do not use an `ssh` binary and do not need sudo on the controller. They call SSM with boto3. The host does not install the app with Ansible. Cloud-init pulls a published image.

```bash
pip install -r requirements-host.txt
```

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
```

Edit the copy: set `backup_bucket_name` to a globally unique name. Leave `enable_aws = true` until the night is over. `key_name`, `ssh_ingress_cidr`, `ssh_ingress_cidrs`, and `extra_ssh_public_keys` are still in the example so an existing tfvars file applies. Nothing reads them. Port 22 is not opened, and the boot script disables sshd. Session Manager is the break-glass shell. Put `CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` in the environment or in the gitignored `.env`. `CLERK_DEV_BYPASS` must be off or `up` refuses to run. Copy `terraform/app.auto.tfvars.example` to `terraform/app.auto.tfvars` and set `app_image` to the `sha-` tag or digest from the image workflow. Do not put `app_image` or the Clerk keys in `terraform.tfvars`. That file is shared with stacks that do not declare those variables, and the keys would be committed if that file were ever tracked. `up` passes the keys as `TF_VAR_clerk_publishable_key` and `TF_VAR_clerk_secret_key`.

```bash
PYTHONPATH=src python3 -m dutch_blitz_host up
```

`up` creates the backup bucket if needed, starts the instance, waits until the SSM agent reports `PingStatus=Online` (one to two minutes), and waits until cloud-init has pulled the image, restored the latest snapshot, and started Caddy. It then prints an `https://` URL. Send that URL to the table. Caddy on the host serves it with a certificate and proxies to the container, which listens on `127.0.0.1:8000` only. The agent calls out to SSM. Nothing accepts a connection on port 22, and the boot script disables sshd. Session Manager is the break-glass shell.

With these lines in `terraform.tfvars`, the URL is always **https://blitz.sixofonedev.com**:

```hcl
dns_zone_name   = "sixofonedev.com"
dns_record_name = "blitz"
```

`up` creates an A record for that name, TTL 60 seconds, pointing at the new public IP. It waits until the authoritative nameservers and the public resolvers (1.1.1.1 and 8.8.8.8) answer with that IP. The boot script does its own shorter check against DNS over HTTPS and does not start Caddy until that name is the instance's public IP. That laptop wait is the Mac's path, and it is the default. On a network that intercepts port 53, set `DUTCH_BLITZ_DNS_MODE=doh`. The wait then uses DNS over HTTPS (`https://dns.google/resolve`, then Cloudflare) and, when the AWS CLI can read the zone, checks the A record with Route 53 `ListResourceRecordSets`. The same HTTPS path runs on its own when a classic lookup returns an address in `198.18.0.0/15` or an empty answer. A real but stale address stays on port 53. `down` deletes the A record with the instance. The hosted zone stays. Terraform looks the zone up by name and does not create it, destroy it, or change its NS, SOA, or the Clerk records (`clerk`, `accounts`, `clkmail`, `clk._domainkey`, `clk2._domainkey`).

Add `https://blitz.sixofonedev.com` once as an allowed origin in the [Clerk dashboard](https://dashboard.clerk.com) (and under Domains if the production instance asks). Later nights reuse that origin. The controller `.env` holds the production `pk_live_` / `sk_live_` keys. The app reads the Frontend API host from the publishable key, which is `clerk.sixofonedev.com`.

Leave `dns_zone_name` empty to keep the previous hostname, `<public-ipv4>.sslip.io`. That name changes every night, so the Clerk allowed origin has to be updated to match. Set `domain` only when you are pointing some other name at the instance yourself.

The boot script uploads the local score file before it restores, so running it again does not roll the night backward. The first S3 call can fail while the instance role is still becoming usable. The script retries that call. If it still fails, run `PYTHONPATH=src python3 -m dutch_blitz_host configure` again. `configure` applies the game-night stack (the instance is not replaced; user data changes are ignored) and re-runs the boot script cloud-init stored on the instance. It does not send that script. SSM keeps command parameters, and the script contains the Clerk keys. A new `app_image` pin, and the idle numbers, are written to `/etc/dutch-blitz/configure.env`. The on-instance script reads that file. A host that is already up does not pull a new `app_image` on `up`. `up` only waits for the first-boot cloud-init. Put the new pin in `terraform/app.auto.tfvars` and run `configure`. An instance booted before this change has a script that does not read that file. `down` and `up` replace it.

When the night is over:

```bash
PYTHONPATH=src python3 -m dutch_blitz_host down
```

`down` uploads a final consistent snapshot (Python's `sqlite3` backup API), then destroys the instance. The S3 bucket is a separate Terraform state with `prevent_destroy`, so this command leaves it in place. A timer on the host also uploads about every 15 minutes. `PYTHONPATH=src python3 -m dutch_blitz_host backup` uploads once without tearing the host down.

If the instance already shut itself down, `down` does not try the backup. It still deletes the blitz A record and the Terraform state. `--skip-backup` remains the manual escape when SSM fails and describe cannot tell whether the instance is gone. Scores saved after the last upload are not copied in that case. A tainted or un-id'd instance is looked up by the `Project=dutch-blitz` tag and backed up over SSM. The public IP is not required.

### If the server is left up

A timer on the instance checks about every 15 minutes. It does not need your laptop or the assistant to be online.

Idle means no HTTP request to the scorekeeper for `idle_shutdown_minutes` (default 90). Caddy's access log and a stamp the app writes on each request both count. The newer one wins. Let's Encrypt challenge requests do not count. Opening the site, including a refresh, resets this clock.

Uptime is how long this boot has lasted. At `uptime_shutdown_minutes` (default 8 hours, 480 minutes) the host shuts down even if people are still scoring.

The warning email goes out once when idle reaches `idle_warn_minutes` (default 60) or uptime reaches `uptime_warn_minutes` (default 7 hours, 420). It says how long the host has been up, how long it has been idle, when it will shut down, and how to keep it running. After a snooze, the uptime warning can send once more. Using the site re-arms the idle warning.

Mail is an SNS topic named `<name_prefix>-idle` (default `dutch-blitz-idle`) in `terraform/game-role`. The subscription is `alert_email` from the gitignored tfvars. Confirm AWS's email once. `down` does not delete the topic, so that confirmation lasts across nights.

To keep a long game going, open the instance in Session Manager (the console, with your own credentials; the bot cannot start a session) and run:

```bash
sudo /usr/local/bin/dutch-blitz-idle snooze
```

That adds `idle_snooze_minutes` (default 2 hours) to the uptime deadline. Run it again if you need more. It does not reset the idle clock. There is no web address that snoozes or shuts the server down. The app cannot write the snooze file. That file is root-owned.

When a limit is reached the host runs the same backup command the 15-minute backup timer uses. If the upload fails, the host stays up and emails the failure once. If the upload succeeds, it emails that it is shutting down and runs `shutdown -h now`.

The instance is launched with `instance_initiated_shutdown_behavior = "terminate"`, so that power-off deletes the instance and the root volume. Compute and disk billing stop. A stop would leave the instance in Terraform state. The next `up` would see it and would not boot a fresh one, and stop releases the public IP, so the blitz name would point nowhere. The instance role is not allowed to call `ec2:TerminateInstances` or `ec2:StopInstances`.

After a self-terminate, the blitz A record and the game-night Terraform state still describe the dead instance. Phones will fail to connect. Run `down` when you see the shutdown email. It removes the DNS record and the state. The next `up` creates a new instance. Until one of those, the name is stale.

The check can be up to `idle_check_minutes` late (default 15). Change the numbers in `terraform/terraform.tfvars`, then run `configure` on a host that is already up, or let the next `up` install them. `configure` rewrites `/etc/dutch-blitz/idle.env` and the timer. `up` does not apply `terraform/game-role`.

A night that is already running when this is first applied does not yet have the timer, and it does not yet terminate on shutdown. `down` it, apply `terraform/game-role`, confirm the email, then `up`. AWS provider v6 calls `ec2:ModifyInstanceAttribute` after `RunInstances` to set `instanceInitiatedShutdownBehavior`, so the bot policy allows that one attribute on instances tagged `Project=dutch-blitz`. It does not allow an instance-type change. If apply fails on that call, the instance is tainted and the blitz record was not created. `down` still finds it by the `Project` tag and uploads the final snapshot before destroy. Details are in [docs/bot-iam.md](docs/bot-iam.md).

Owner steps after this change is merged, with your own credentials:

1. In gitignored `terraform/terraform.tfvars`, set `alert_email` to your address. `alerts@example.com` is rejected. The optional minute settings are in `terraform/terraform.tfvars.example`. Do not commit the address.
2. Apply only `terraform/game-role`. That creates the topic, the email subscription, and an instance-role policy that is `sns:Publish` on that topic:

```bash
terraform -chdir=terraform/game-role init -backend-config=../backend.hcl
terraform -chdir=terraform/game-role apply -var-file=../terraform.tfvars
```

3. Open the AWS confirmation message and confirm the subscription. Until you do, publishes succeed and no mail arrives.
4. Do not re-apply `terraform/iam-bot` only because the idle timer landed. That change did not add SNS to the bot policy. `up` and `down` do not call SNS. `down` uses `ec2:DescribeInstances`, which the bot already has. Re-apply `terraform/iam-bot` when the bot policy JSON changes. The shutdown-behavior allow is one of those changes: until that apply, the bot's `up` fails on `ec2:ModifyInstanceAttribute` and leaves the instance tainted. Details are in [docs/bot-iam.md](docs/bot-iam.md).

Cloud-init installs the timer. The topic name on the host is `<name_prefix>-idle`. The host builds the ARN from that name, the region, and the account id in the instance metadata. The account id is not in git.

State for every root is an object in one S3 bucket, shared by the Mac and the Linux assistant. The bucket name is not in git. Copy `terraform/backend.hcl.example` to `terraform/backend.hcl` and follow [docs/remote-state.md](docs/remote-state.md) once. Bootstrap `terraform/state` with a gitignored `terraform/state/bootstrap_override.tf` that contains `backend "local" {}`, init, and apply. `init -backend=false` and then apply stops with `Backend initialization required`, because the s3 backend block is still there and was not initialized. Delete the override, then `terraform init -migrate-state -backend-config=backend.hcl` for each root that already has a local `terraform.tfstate`. Compare `terraform state list` after that init with `terraform show` of the state files copied aside before migration. `terraform state list` fails until init has run. `up` and `down` pass that backend file into `terraform init`. CI keeps using `init -backend=false` and `validate`, and does not contact the bucket.

On AWS, before the first `up`:

- Use credentials Terraform and boto3 can see (environment or `~/.aws`). The Mac keeps using your credentials. The Linux assistant uses the bot user below, not yours. Neither machine needs an `ssh` client or sudo.
- The account needs a default VPC in `us-east-2`. The instance has a public IP so the SSM agent can reach the service endpoints. The security group allows HTTP, HTTPS, and all outbound. It does not allow port 22, and the boot script disables sshd. Session Manager stays the break-glass shell.
- Pick a bucket name that is free worldwide. Apply `terraform/backup` once so the bucket exists, or let the first `up` create it. Later nights reuse it.
- Apply `terraform/game-role` yourself, after the bucket exists and before the first `up`. That root is the instance role, the instance profile, the SSM agent policy, and the idle-shutdown SNS topic. `up` only looks the profile up by name. `down` does not delete the role or the topic. Set `alert_email` in the gitignored tfvars and confirm the subscription email once. The steps, including a night that still has the role in the game-night state, are in [docs/remote-state.md](docs/remote-state.md). The SSM policy is inline, not `AmazonSSMManagedInstanceCore`, so the instance cannot read Parameter Store. It still allows Session Manager from your console. Details are in [docs/bot-iam.md](docs/bot-iam.md).
- `user_data_replace_on_change` is false, and Terraform ignores later user-data changes, so editing the boot script does not stop or replace a night that is already up. `key_name` is also ignored on an instance that already exists, because dropping the old key pair would replace it. New instances are created with no key pair. Cloud-init reads user data on first boot.

### Credentials for the Linux assistant

The assistant's AWS identity is an IAM user named `grok-bot-dutch-blitz`. You create the user by applying `terraform/iam-bot` with your own credentials. The policy and the $10 budget are explained in [docs/bot-iam.md](docs/bot-iam.md). The policy JSON you can read without applying is [docs/grok-bot-dutch-blitz-policy.json](docs/grok-bot-dutch-blitz-policy.json). `${name}` variables in that file are filled from the gitignored tfvars at apply time. Do not put the real account id, bucket names, hosted zone id, or alert email in the repo.

Terraform does not create the access key. An `aws_iam_access_key` resource would write the secret into the state file. After the user exists, create the key yourself and store it in the assistant's secret store (its environment, or a `0600` credentials file on that machine). It never goes in git, in `terraform.tfvars`, or in `backend.hcl`.

Console: IAM, Users, `grok-bot-dutch-blitz`, Security credentials, Create access key. Choose the CLI use case. The secret is shown once.

CLI, with your credentials, not the bot's:

```bash
aws iam create-access-key --user-name grok-bot-dutch-blitz
```

On the assistant, point Terraform and the AWS CLI at that key. A credentials file is the usual place:

```bash
mkdir -p ~/.aws
chmod 700 ~/.aws
# ~/.aws/credentials is mode 0600. This profile is the bot, not your admin user.
aws configure --profile grok-bot-dutch-blitz
export AWS_PROFILE=grok-bot-dutch-blitz
export AWS_DEFAULT_REGION=us-east-2
aws sts get-caller-identity
```

`get-caller-identity` should print the bot user. Then `up` and `down` on that machine use the bot profile and DoH. Install boto3 there (`pip install -r requirements-host.txt`). No SSH key and no sudo:

```bash
export AWS_PROFILE=grok-bot-dutch-blitz
export AWS_DEFAULT_REGION=us-east-2
export DUTCH_BLITZ_DNS_MODE=doh
PYTHONPATH=src python3 -m dutch_blitz_host up
```

`configure`, `backup`, and `down` use the same credentials. SSM Run Command is the channel:

```bash
PYTHONPATH=src python3 -m dutch_blitz_host down
```

Leave `DUTCH_BLITZ_DNS_MODE` unset on the Mac. Port 53 there is the default. Your Mac does not set the bot profile. You keep applying `terraform/state`, `terraform/iam-bot`, `terraform/game-role`, and `terraform/gha-deployer` with your own user. The bot is denied those actions. `up` applies the backup bucket and the game-night instance. It does not apply the role or the deployer. Apply `terraform/iam-bot` again with your own credentials after the bot policy changes. Until that apply, `SendCommand` stays denied and the bot's refresh of the backup bucket stays denied. Apply `terraform/game-role` as well so the instance role can register with SSM. `ssm:SendCommand` lets the bot, and the GitHub deployer that attaches the same policies, run root commands on instances tagged `Project=dutch-blitz`. It does not grant `ssm:StartSession`.

An IAM user can hold two access keys. Rotate without a gap:

1. Create a second key with the command above.
2. Put the new key in the assistant's secret store and restart whatever holds the old environment.
3. On the assistant, `aws sts get-caller-identity` and a `dutch_blitz_host apply --dry-run` should succeed with the new key.
4. Disable the old key, confirm the assistant still works, then delete it:

```bash
aws iam update-access-key \
  --user-name grok-bot-dutch-blitz \
  --access-key-id AKIA_OLD_KEY_ID \
  --status Inactive
aws iam delete-access-key \
  --user-name grok-bot-dutch-blitz \
  --access-key-id AKIA_OLD_KEY_ID
```

Those two commands use your credentials. The bot policy has no `iam:CreateAccessKey`, `iam:UpdateAccessKey`, or `iam:DeleteAccessKey`, so a leaked bot key cannot rotate itself or mint another one. To revoke immediately, run the `Inactive` command, or delete the key, or delete the user in the IAM console. `down` on the Mac with your own credentials still works after the bot key is dead. The state is in S3 either way.

The budget email is a variable in `terraform/iam-bot/terraform.tfvars`. Confirm AWS's subscription message or the $10 alarm never sends.

To retire the bucket, edit `terraform/backup/main.tf` and remove the `prevent_destroy = true` line. Empty the bucket, then destroy only that stack:

```bash
terraform -chdir=terraform/backup destroy -var-file=../terraform.tfvars
```

`down` never runs that command. `--skip-backup` exists for a host that is already gone; scores saved after the last upload are not copied.

## Game night button

[`.github/workflows/game-night.yml`](.github/workflows/game-night.yml) is the Actions button named **Game night**. It runs `dutch_blitz_host` with `up`, `down`, `backup`, or `status`. It stores no AWS access key. GitHub's OIDC token assumes `dutch-blitz-gha-deployer`, and that role attaches the same two managed policies as `grok-bot-dutch-blitz`. The session lasts at most one hour. The workflow's top-level permissions are empty. The job has `id-token: write`, `contents: read`, and `actions: read`.

The role trusts `token.actions.githubusercontent.com` when `aud` is `sts.amazonaws.com` and `sub` is one of `repo:sixOfOne@50211093/dutch-blitz@1413603165:environment:game-night` or `repo:sixOfOne/dutch-blitz:environment:game-night`. This repository was created on 2026-10-10 and its OIDC customization reports `use_immutable_subject`, so the live token uses the id form. The name form is what GitHub emits when immutable subjects are off. Both values are this repository and the `game-night` environment. STS does not accept `workflow_ref`, `event_name`, or `ref` as condition keys. The environment's selected-branch rule is what keeps the run on `main`. Re-apply `terraform/gha-deployer` after a trust-policy change, with your own credentials, before the next Game night run.

Do these steps yourself, in this order, after this change is on `main`. Do not use the bot access key.

1. Confirm `terraform/iam-bot` is already applied, so the managed policies `grok-bot-dutch-blitz-compute` and `grok-bot-dutch-blitz-records` exist. This root only attaches them. If you renamed the bot user, the name in the next file has to match.
2. On the Mac, with your own AWS credentials, copy the example and set `enable_aws = true`. If the account already has an OIDC provider for `token.actions.githubusercontent.com`, set `github_oidc_provider_arn` to that ARN (`aws iam list-open-id-connect-providers`). Leave it empty and apply creates the provider. A second provider for the same URL fails.

```bash
cp terraform/gha-deployer/terraform.tfvars.example terraform/gha-deployer/terraform.tfvars
terraform -chdir=terraform/gha-deployer init -backend-config=../backend.hcl
terraform -chdir=terraform/gha-deployer apply -var-file=terraform.tfvars
```

3. Read the `role_arn` output. It looks like `arn:aws:iam::<account>:role/dutch-blitz-gha-deployer`.
4. On GitHub, open **Settings → Environments → New environment**. Name it exactly `game-night`.
5. Turn on **Required reviewers** and add yourself. Save before any workflow run. A first run that creates the environment for you is not reviewed.
6. Under **Deployment branches**, choose **Selected branches** and add `main`.
7. On that environment, add a **variable** (not a secret) named `AWS_ROLE_ARN`. The value is the `role_arn` from step 3.
8. On that environment, add these **secrets**. GitHub masks them. The workflow writes the two files under the runner temp directory, mode `0600`, and does not echo them. Leave `app_image` and both Clerk keys out of `TERRAFORM_TFVARS`. The workflow sets `TF_VAR_app_image` to a published `sha-` tag: the tag of `origin/main` when that image exists, otherwise the last image published by the Image workflow. The optional `app_image` input overrides that pin. The run stops before Terraform when the manifest is missing.
   - `CLERK_PUBLISHABLE_KEY`
   - `CLERK_SECRET_KEY`
   - `TERRAFORM_TFVARS`: the body of the gitignored `terraform/terraform.tfvars`
   - `BACKEND_HCL`: the body of the gitignored `terraform/backend.hcl`
9. **Actions → Game night → Run workflow**. Branch `main`, action `status`. Approve the review when GitHub asks. Then `up`, `backup`, or `down` the same way. One run at a time: a second click waits, and it does not cancel the run that is already going. The job limit is 50 minutes, inside the one-hour role session.

`status` prints the public outputs and the SSM ping. It does not print `cloud_init`. A change to `docs/grok-bot-dutch-blitz-policy.json` is applied with `terraform/iam-bot` only. The deployer role uses those same policies, so it sees the new document without another apply here. Re-apply `terraform/gha-deployer` when the trust policy or the OIDC provider setting changes. `ssm:SendCommand` on this role runs root commands on instances tagged `Project=dutch-blitz`, the same as the bot. There is no `ssm:StartSession`.

`terraform.tfvars`, `app.auto.tfvars`, `backend.hcl`, Terraform state, `.env`, and `*.pem` files are gitignored. The example tfvars and `backend.hcl.example` have placeholders only. Clerk keys are in the instance user data and in the Terraform state object. The state bucket is private. Do not put the keys in a file you might commit.

## Rules encoded here

Each round, for every player:

```text
score = (Dutch cards) − 2 × (Blitz cards left)
```

Dutch cards are the ones you played out (0–40, the size of a player deck). Blitz cards left are the ones still in the Blitz pile (0–10). A round score can be negative, and it is added to that player's running total.

The game ends when you finish the round in which at least one total reaches or passes the target. You always enter every player for that round before the result is decided. The winner is the highest total. If two or more players share that highest total, they are co-winners and the game stops. There is no extra tiebreaker round.

The target is chosen only when the game is created. It must be a whole number from 1 to 999. Leaving it blank, or leaving the field at its starting value, uses 75.

Player names are trimmed. Upper and lower case do not make two different players. Empty name rows are ignored when they are at the end of the list; a blank in the middle is rejected.

Each player on a new game can pick a deck color and icon. Choosing a color fills in that color's icon, and the icon can be changed. The same color cannot be used twice in one game. Lists, score rows, and the scoreboard show the icon and the color name together, so a deck is not identified by color alone.

The pairs are the ones named on the [official sticker packs](https://dutchblitz.com/products/dutch-blitz-sticker-pack): red carriage, blue plow, yellow pail, green pump, orange hat, navy blue wheelbarrow, pink water can, and purple butter churn. The sticker list spells the yellow symbol "Pale"; the [rules](https://dutchblitz.com/wp-content/uploads/Dutch_Blitz_Directions_Web.pdf) call it a pail. The blue expansion swaps those four symbols across the same colors, but the maker does not publish which symbol lands on which color, so those decks are not a second set of choices. Games saved before this change still open. Those players have no color or icon.

## Correcting a round

**Fix last round** reloads the card counts so you can change a mistype without retyping the whole table. Saving it recalculates totals and can finish the game or reopen it.

**Undo last round** deletes that round. Undoing the round that ended the game resumes play. Earlier rounds stay as they were.

## Layout

```text
app/main.py        pages and the sign-in gate
app/auth.py        Clerk session checks
app/config.py      environment variables
app/scoring.py     round score and game-end rules
app/decks.py       deck colors, icons, and the official pairs
app/store.py       SQLite, owners, invite links, and claimed seats
app/views.py       totals and the words on the page
app/templates/     server-rendered HTML
app/static/        CSS, score form, and Clerk cookie refresh
src/dutch_blitz_host/  game-night CLI (apply, configure, backup, destroy, idle shutdown)
Dockerfile         scorekeeper image, published to ghcr.io/sixofone/dutch-blitz
terraform/         EC2 host, cloud-init, and blitz A record, destroyed by down; looks up the instance profile
terraform/templates/cloud-init.sh.tftpl  boot script Terraform renders into user data
terraform/backup/  S3 snapshot bucket, kept across nights
terraform/game-role/ instance role, instance profile, and idle SNS topic (you apply this; down does not)
terraform/gha-deployer/ GitHub OIDC deployer role for the Game night workflow (you apply this)
terraform/state/   bootstrap bucket for Terraform state (you apply this)
terraform/iam-bot/ bot user, least-privilege policy, and $10 budget (you apply this)
docs/remote-state.md  one-time migration from local tfstate
docs/bot-iam.md    what the bot may do, and what could not be scoped tightly
docs/repo-security.md  GitHub settings to turn on by hand, and what each one stops
.github/workflows/ci.yml  ruff, mypy, pytest, tflint, and terraform validate
.github/workflows/game-night.yml  workflow_dispatch up, down, backup, and status
.github/workflows/image.yml  build the image; push it on main
.github/dependabot.yml  weekly pull requests for dependency updates
SECURITY.md        how to report a vulnerability in private
tests/             pytest
AGENTS.md          notes for an assistant changing this repo
```

The pages are server-rendered. JavaScript shows the round score while you type, reveals extra name fields, keeps a scroll wheel from changing a focused number, and, when Clerk is configured, mounts sign-in and refreshes the session cookie.
