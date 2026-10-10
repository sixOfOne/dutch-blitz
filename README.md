# Dutch Blitz

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
pip install -r requirements-dev.txt
pytest
```

The tests cover negative round scores, running totals, the default target and a custom target, the 2-player and 8-player limits, ties, saved history, undo, editing the last round, sign-in enforcement, invite links, and that an outsider cannot see a game. Token checks in tests are stubbed, so the suite does not call Clerk.

## Configuration

Copy `.env.example` to `.env` if you want the variables loaded automatically. `python -m app` reads that file without overriding variables you already exported. `.env` is gitignored. Do not put real keys in the repository.

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

From the repository root, with [Terraform](https://developer.hashicorp.com/terraform/install) 1.5 or newer and `ansible-playbook` on your PATH. No extra Python package is required. The controller uses the standard library.

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

On AWS, before the first `up`:

- Use credentials Terraform can see (environment or `~/.aws`).
- The account needs a default VPC in `us-east-2`.
- Create an EC2 key pair named `neo-atari` in `us-east-2` if it is not already there. Key pairs are regional. The private key this CLI uses is `~/.ssh/neo-atari.pem`.
- Pick a bucket name that is free worldwide. The first `up` creates it. Later nights reuse it.

To retire the bucket, edit `terraform/backup/main.tf` and remove the `prevent_destroy = true` line. Empty the bucket, then destroy only that stack:

```bash
terraform -chdir=terraform/backup destroy -var-file=../terraform.tfvars
```

`down` never runs that command. `--skip-backup` exists for a host that is already gone; scores saved after the last upload are not copied.

`terraform.tfvars`, Terraform state, and `ansible/inventory/aws.yml` are gitignored. The example tfvars file has placeholders only.

## Rules encoded here

Each round, for every player:

```text
score = (Dutch cards) − 2 × (Blitz cards left)
```

Dutch cards are the ones you played out (0–40, the size of a player deck). Blitz cards left are the ones still in the Blitz pile (0–10). A round score can be negative, and it is added to that player's running total.

The game ends when you finish the round in which at least one total reaches or passes the target. You always enter every player for that round before the result is decided. The winner is the highest total. If two or more players share that highest total, they are co-winners and the game stops. There is no extra tiebreaker round.

The target is chosen only when the game is created. It must be a whole number from 1 to 999. Leaving it blank, or leaving the field at its starting value, uses 75.

Player names are trimmed. Upper and lower case do not make two different players. Empty name rows are ignored when they are at the end of the list; a blank in the middle is rejected.

## Correcting a round

**Fix last round** reloads the card counts so you can change a mistype without retyping the whole table. Saving it recalculates totals and can finish the game or reopen it.

**Undo last round** deletes that round. Undoing the round that ended the game resumes play. Earlier rounds stay as they were.

## Layout

```text
app/main.py        pages and the sign-in gate
app/auth.py        Clerk session checks
app/config.py      environment variables
app/scoring.py     round score and game-end rules
app/store.py       SQLite, owners, invite links, and claimed seats
app/views.py       totals and the words on the page
app/templates/     server-rendered HTML
app/static/        CSS, score form, and Clerk cookie refresh
src/dutch_blitz_host/  game-night CLI (apply, configure, backup, destroy)
terraform/         EC2 host, destroyed by down
terraform/backup/  S3 snapshot bucket, kept across nights
ansible/           install, Caddy, sshd, backup timer
tests/             pytest
```

The pages are server-rendered. JavaScript shows the round score while you type, reveals extra name fields, keeps a scroll wheel from changing a focused number, and, when Clerk is configured, mounts sign-in and refreshes the session cookie.
