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
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

The tests cover negative round scores, running totals, the default target and a custom target, the 2-player and 8-player limits, ties, saved history, undo, editing the last round, sign-in enforcement, invite links, deck color and icon, the migration that adds those columns, and that an outsider cannot see a game. Token checks in tests are stubbed, so the suite does not call Clerk.

## Continuous integration

Every pull request, and every push to `main`, runs the [CI workflow](.github/workflows/ci.yml). A failure shows up on the pull request. The workflow has no Clerk keys and no AWS credentials. It does not run `terraform plan` or `terraform apply`, and it does not create resources.

Two checks run in parallel:

- **Python tests** runs the suite on Python 3.11, the version the game-night host installs. pip caches the wheels from `requirements.txt` and `requirements-dev.txt`.
- **Terraform and Ansible** checks formatting with `terraform fmt -check`, then runs `terraform init -backend=false` and `terraform validate` in `terraform/`, `terraform/backup/`, `terraform/state/`, `terraform/iam-bot/`, and `terraform/game-role/` with `enable_aws` false. `-backend=false` does not read `backend.hcl` and does not call the state bucket. It parses `ansible/playbook.yml` with `--syntax-check` against `ansible/inventory/syntax-check.yml`, then runs `ansible-lint`. That inventory is only a stand-in. The real one is written on a game night and is gitignored.

A newer push to the same branch cancels the run that is still going, so an outdated commit does not spend the rest of its minutes.

Run the same checks from the repository root. Terraform 1.10 or newer is required, because the S3 backend locks state with `use_lockfile` (CI uses 1.16.5). The Ansible tools below install on Python 3.11.

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest

terraform fmt -check -diff -recursive
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

pip install 'ansible-core==2.19.14' 'ansible-lint==26.9.0'
ansible-playbook ansible/playbook.yml --syntax-check -i ansible/inventory/syntax-check.yml
ansible-lint ansible/playbook.yml --offline
```

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

## Game night on EC2

A night is one `t3.small` in `us-east-2` (Amazon Linux 2023) for a few hours, then the instance is deleted. Scores are copied to an S3 bucket that stays. Three hours is about **$0.10**: the instance is about $0.021 an hour, and the disk and a small SQLite upload add a few cents. The bucket is pennies a month and is not part of that teardown.

From the repository root, with [Terraform](https://developer.hashicorp.com/terraform/install) 1.10 or newer and `ansible-playbook` on your PATH. No extra Python package is required. The controller uses the standard library. 1.10 is the first release that locks S3 state with `use_lockfile`. CI uses 1.16.5.

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
```

Edit the copy: set `ssh_ingress_cidr` to your public IP as a `/32`, and set `backup_bucket_name` to a globally unique name. Leave `enable_aws = true` until the night is over. Put `CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` in the environment or in the gitignored `.env`. `CLERK_DEV_BYPASS` must be off or `up` refuses to run.

```bash
PYTHONPATH=src python3 -m dutch_blitz_host up
```

`up` creates the backup bucket if needed, starts the instance, installs the app, restores the latest snapshot, and prints an `https://` URL. Send that URL to the table. Caddy serves it with a certificate.

With these lines in `terraform.tfvars`, the URL is always **https://blitz.sixofonedev.com**:

```hcl
dns_zone_name   = "sixofonedev.com"
dns_record_name = "blitz"
```

`up` creates an A record for that name, TTL 60 seconds, pointing at the new public IP. It waits until the authoritative nameservers and the public resolvers (1.1.1.1 and 8.8.8.8) answer with that IP, then Caddy requests a certificate. `down` deletes the A record with the instance. The hosted zone stays. Terraform looks the zone up by name and does not create it, destroy it, or change its NS, SOA, or the Clerk records (`clerk`, `accounts`, `clkmail`, `clk._domainkey`, `clk2._domainkey`).

Add `https://blitz.sixofonedev.com` once as an allowed origin in the [Clerk dashboard](https://dashboard.clerk.com) (and under Domains if the production instance asks). Later nights reuse that origin. The controller `.env` holds the production `pk_live_` / `sk_live_` keys. The app reads the Frontend API host from the publishable key, which is `clerk.sixofonedev.com`.

Leave `dns_zone_name` empty to keep the previous hostname, `<public-ipv4>.sslip.io`. That name changes every night, so the Clerk allowed origin has to be updated to match. Set `domain` only when you are pointing some other name at the instance yourself.

If this host already has a score file, configure uploads that file before the restore so a second configure does not roll the night backward. If the first S3 call fails, the instance role can take a few seconds to become usable; run `PYTHONPATH=src python3 -m dutch_blitz_host configure` again.

When the night is over:

```bash
PYTHONPATH=src python3 -m dutch_blitz_host down
```

`down` uploads a final consistent snapshot (Python's `sqlite3` backup API), then destroys the instance. The S3 bucket is a separate Terraform state with `prevent_destroy`, so this command leaves it in place. A timer on the host also uploads about every 15 minutes. `PYTHONPATH=src python3 -m dutch_blitz_host backup` uploads once without tearing the host down.

State for every root is an object in one S3 bucket, shared by the Mac and the Linux assistant. The bucket name is not in git. Copy `terraform/backend.hcl.example` to `terraform/backend.hcl` and follow [docs/remote-state.md](docs/remote-state.md) once: bootstrap `terraform/state`, then `terraform init -migrate-state -backend-config=backend.hcl` for each root that already has a local `terraform.tfstate`, then `terraform state list`. Keep the old local state files as backups. `up` and `down` pass that backend file into `terraform init`. CI keeps using `init -backend=false` and does not contact the bucket.

On AWS, before the first `up`:

- Use credentials Terraform can see (environment or `~/.aws`). The Mac keeps using your credentials. The Linux assistant uses the bot user below, not yours.
- The account needs a default VPC in `us-east-2`.
- Create an EC2 key pair named `neo-atari` in `us-east-2` if it is not already there. Key pairs are regional. The private key this CLI uses is `~/.ssh/neo-atari.pem`. The assistant needs its own copy of that private key, mode `0600`, and that file is not committed.
- Pick a bucket name that is free worldwide. Apply `terraform/backup` once so the bucket exists, or let the first `up` create it. Later nights reuse it.
- Apply `terraform/game-role` yourself, after the bucket exists and before the first `up`. That root is the instance role and instance profile. `up` only looks the profile up by name. `down` does not delete it. The steps, including a night that still has the role in the game-night state, are in [docs/remote-state.md](docs/remote-state.md).
- If the assistant also runs `up` or `down`, add that machine's public IP as a `/32` to `ssh_ingress_cidrs` in the gitignored `terraform/terraform.tfvars`. `ssh_ingress_cidr` stays the first machine. HTTP and HTTPS stay open.

### Credentials for the Linux assistant

The assistant's AWS identity is an IAM user named `grok-bot-dutch-blitz`. You create the user by applying `terraform/iam-bot` with your own credentials. The policy and the $10 budget are explained in [docs/bot-iam.md](docs/bot-iam.md). The policy JSON you can read without applying is [docs/grok-bot-dutch-blitz-policy.json](docs/grok-bot-dutch-blitz-policy.json). Placeholders in that file are filled from the gitignored tfvars at apply time. Do not put the real account id, bucket names, hosted zone id, or alert email in the repo.

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

`get-caller-identity` should print the bot user. Then `PYTHONPATH=src python3 -m dutch_blitz_host up` uses that profile. Your Mac does not set this profile. You keep applying `terraform/state`, `terraform/iam-bot`, and `terraform/game-role` with your own user. The bot is denied those actions. `up` applies the backup bucket and the game-night instance. It does not apply the role.

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

`terraform.tfvars`, `backend.hcl`, Terraform state, generated files in `ansible/inventory/` (except `syntax-check.yml`), `.env`, and `*.pem` files are gitignored. The example tfvars and `backend.hcl.example` have placeholders only.

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
src/dutch_blitz_host/  game-night CLI (apply, configure, backup, destroy)
terraform/         EC2 host and blitz A record, destroyed by down; looks up the instance profile
terraform/backup/  S3 snapshot bucket, kept across nights
terraform/game-role/ instance role and profile (you apply this; down does not)
terraform/state/   bootstrap bucket for Terraform state (you apply this)
terraform/iam-bot/ bot user, least-privilege policy, and $10 budget (you apply this)
docs/remote-state.md  one-time migration from local tfstate
docs/bot-iam.md    what the bot may do, and what could not be scoped tightly
ansible/           install, Caddy, sshd, backup timer
.github/workflows/ci.yml  tests and static infrastructure checks
tests/             pytest
```

The pages are server-rendered. JavaScript shows the round score while you type, reveals extra name fields, keeps a scroll wheel from changing a focused number, and, when Clerk is configured, mounts sign-in and refreshes the session cookie.
