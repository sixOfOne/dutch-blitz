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

The tests cover negative round scores, running totals, the default target and a custom target, the 2-player and 8-player limits, ties, saved history, undo, and editing the last round.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DUTCH_BLITZ_DB_PATH` | `data/dutch_blitz.sqlite` | SQLite file. The directory is created if needed. |
| `DUTCH_BLITZ_HOST` | `127.0.0.1` | Address the server binds to. |
| `DUTCH_BLITZ_PORT` | `8000` | Port the server listens on. |

The database file is gitignored. There are no secrets in this project. These variables are the hook for a later deploy; this repository does not add cloud infrastructure.

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
app/main.py        pages
app/scoring.py     round score and game-end rules
app/store.py       SQLite
app/views.py       totals and the words on the page
app/templates/     server-rendered HTML
app/static/        CSS and a little form JavaScript
tests/             pytest
```

The pages are server-rendered. The only JavaScript shows the round score while you type, reveals extra name fields, and keeps a scroll wheel from changing a focused number.
