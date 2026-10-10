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

The tests cover negative round scores, running totals, the default target and a custom target, the 2-player and 8-player limits, ties, saved history, undo, editing the last round, sign-in enforcement, and that one account cannot see or change another account's games. Token checks in tests are stubbed, so the suite does not call Clerk.

## Configuration

Copy `.env.example` to `.env` if you want the variables loaded automatically. `python -m app` reads that file without overriding variables you already exported. `.env` is gitignored. Do not put real keys in the repository.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DUTCH_BLITZ_DB_PATH` | `data/dutch_blitz.sqlite` | SQLite file. The directory is created if needed. |
| `DUTCH_BLITZ_HOST` | `127.0.0.1` | Address the server binds to. |
| `DUTCH_BLITZ_PORT` | `8000` | Port the server listens on. |
| `CLERK_PUBLISHABLE_KEY` | unset | Clerk publishable key (`pk_test_...` or `pk_live_...`). Safe to send to the browser. |
| `CLERK_SECRET_KEY` | unset | Clerk secret key (`sk_test_...` or `sk_live_...`). Required on the server. Never sent to the browser. |
| `CLERK_FRONTEND_API` | decoded from the publishable key | Frontend API host, such as `example.clerk.accounts.dev`. |
| `CLERK_ISSUER` | `https://<frontend API>` | Expected `iss` on the session token. |
| `CLERK_JWKS_URL` | `<issuer>/.well-known/jwks.json` | Public keys used to verify the session JWT. |
| `CLERK_AUTHORIZED_PARTIES` | unset | Optional comma-separated origins. When set, the token `azp` claim must be one of them. |
| `CLERK_DEV_BYPASS` | off | Set to `1`, `true`, `yes`, or `on` only on a machine that is not shared. |
| `CLERK_DEV_USER_ID` | `dev-user` | The single account used while the bypass is on. |

The database file is gitignored. These variables are the hook for a later deploy; this repository does not add cloud infrastructure.

## Sign-in

Each signed-in person sees only the games they created. Listing, opening, scoring, fixing, and undoing all check the Clerk user id stored on the game.

1. In the [Clerk dashboard](https://dashboard.clerk.com), create an application.
2. Under API keys, copy the publishable key into `CLERK_PUBLISHABLE_KEY` and the secret key into `CLERK_SECRET_KEY`.
3. Add `http://127.0.0.1:8000` as an allowed origin for that Clerk instance if the dashboard asks for one.
4. From the repository root, run `python -m app` and open http://127.0.0.1:8000.

The pages load Clerk's prebuilt sign-in and user button from the publishable key. The browser keeps the session token in a short-lived `__session` cookie and refreshes it before a same-origin form post or link. The server checks that token on every request except `/static/...`, using Clerk's public JWKS. The secret key stays on the server so a deploy cannot start half-configured; signature checks do not use it.

Until both keys are set, the sign-in page explains what is missing and game routes respond with 401.

### Development bypass

`CLERK_DEV_BYPASS` is off unless you set it. When it is on, the server does not load Clerk and treats every visitor as `CLERK_DEV_USER_ID` (or `dev-user`). A banner on each page says the bypass is on. Everyone who can open the server shares that one account, so turn the flag off before sharing the process or deploying.

### Games saved before accounts

Opening an older SQLite file adds a nullable `owner_id` column. Rows that were already there keep `owner_id` NULL. They stay in the database, they are not deleted, and no account can see or claim them. New games always store the signed-in user's Clerk id.

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
app/store.py       SQLite, including each game's owner
app/views.py       totals and the words on the page
app/templates/     server-rendered HTML
app/static/        CSS, score form, and Clerk cookie refresh
tests/             pytest
```

The pages are server-rendered. JavaScript shows the round score while you type, reveals extra name fields, keeps a scroll wheel from changing a focused number, and, when Clerk is configured, mounts sign-in and refreshes the session cookie.
