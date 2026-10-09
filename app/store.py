"""SQLite persistence for games, players, and rounds."""

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from app.errors import NotFoundError, ScoringError
from app.scoring import (
    DEFAULT_TARGET,
    outcome,
    round_score,
    validate_players,
    validate_target,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY,
    target_score INTEGER NOT NULL CHECK (target_score BETWEEN 1 AND 999),
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'finished'))
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
    seat INTEGER NOT NULL CHECK (seat BETWEEN 1 AND 8),
    UNIQUE (game_id, seat),
    UNIQUE (game_id, name)
);

CREATE TABLE IF NOT EXISTS rounds (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    round_number INTEGER NOT NULL CHECK (round_number >= 1),
    created_at TEXT NOT NULL,
    UNIQUE (game_id, round_number)
);

CREATE TABLE IF NOT EXISTS round_entries (
    id INTEGER PRIMARY KEY,
    round_id INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    dutch_cards INTEGER NOT NULL CHECK (dutch_cards BETWEEN 0 AND 40),
    blitz_left INTEGER NOT NULL CHECK (blitz_left BETWEEN 0 AND 10),
    score INTEGER NOT NULL CHECK (score = dutch_cards - (2 * blitz_left)),
    UNIQUE (round_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_players_game ON players(game_id);
CREATE INDEX IF NOT EXISTS idx_rounds_game ON rounds(game_id);
CREATE INDEX IF NOT EXISTS idx_entries_round ON round_entries(round_id);
"""


@dataclass
class Player:
    id: int
    name: str
    seat: int


@dataclass
class RoundEntry:
    player_id: int
    dutch_cards: int
    blitz_left: int
    score: int


@dataclass
class Round:
    number: int
    entries: list[RoundEntry]


@dataclass
class Game:
    id: int
    target_score: int
    status: str
    created_at: str
    players: list[Player]
    rounds: list[Round]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class GameStore:
    """Read and write the score book. One connection per operation."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def init(self) -> None:
        path = Path(self.db_path)
        if path.parent != Path("."):
            path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def create_game(self, names: list[str], target_score: int = DEFAULT_TARGET) -> int:
        cleaned = validate_players(names)
        target = validate_target(target_score)
        with self._transaction() as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO games (target_score, created_at, status)
                    VALUES (?, ?, 'active')
                    """,
                    (target, utc_now()),
                )
                game_id = cursor.lastrowid
                if game_id is None:
                    raise ScoringError("Could not save that game.")
                for seat, name in enumerate(cleaned, start=1):
                    conn.execute(
                        """
                        INSERT INTO players (game_id, name, seat)
                        VALUES (?, ?, ?)
                        """,
                        (game_id, name, seat),
                    )
            except sqlite3.IntegrityError as exc:
                raise ScoringError(
                    "Those player names could not be saved. Use a different name for each player."
                ) from exc
        return int(game_id)

    def get_game(self, game_id: int) -> Game | None:
        with self._connect() as conn:
            return self._load(conn, game_id)

    def list_games(self) -> list[Game]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id FROM games ORDER BY id DESC").fetchall()
            return [game for row in rows if (game := self._load(conn, row["id"])) is not None]

    def record_round(self, game_id: int, counts: Mapping[int, tuple[int, int]]) -> Game:
        """Append a round. Refuses to score a game that is already over."""
        with self._transaction() as conn:
            game_row, players = self._locked_game(conn, game_id)
            if game_row["status"] != "active":
                raise ScoringError("This game is already finished.")
            scored = self._score_counts(players, counts)
            next_number = conn.execute(
                """
                SELECT COALESCE(MAX(round_number), 0) + 1 AS next_number
                FROM rounds
                WHERE game_id = ?
                """,
                (game_id,),
            ).fetchone()["next_number"]
            cursor = conn.execute(
                """
                INSERT INTO rounds (game_id, round_number, created_at)
                VALUES (?, ?, ?)
                """,
                (game_id, next_number, utc_now()),
            )
            round_id = cursor.lastrowid
            conn.executemany(
                """
                INSERT INTO round_entries
                    (round_id, player_id, dutch_cards, blitz_left, score)
                VALUES (?, ?, ?, ?, ?)
                """,
                [(round_id, player_id, dutch, blitz, score) for player_id, dutch, blitz, score in scored],
            )
            self._apply_status(conn, game_id, game_row["target_score"])
        game = self.get_game(game_id)
        if game is None:
            raise NotFoundError("That game isn't in the score book.")
        return game

    def replace_last_round(self, game_id: int, counts: Mapping[int, tuple[int, int]]) -> Game:
        """Replace the card counts on the last round and recalculate the result.

        Editing can finish a game or reopen one, because the target is checked
        again from the corrected totals.
        """
        with self._transaction() as conn:
            game_row, players = self._locked_game(conn, game_id)
            last = self._last_round_row(conn, game_id, missing="There is no round to fix.")
            scored = self._score_counts(players, counts)
            for player_id, dutch, blitz, score in scored:
                updated = conn.execute(
                    """
                    UPDATE round_entries
                    SET dutch_cards = ?, blitz_left = ?, score = ?
                    WHERE round_id = ? AND player_id = ?
                    """,
                    (dutch, blitz, score, last["id"], player_id),
                )
                if updated.rowcount != 1:
                    raise ScoringError("Could not update that round.")
            self._apply_status(conn, game_id, game_row["target_score"])
        game = self.get_game(game_id)
        if game is None:
            raise NotFoundError("That game isn't in the score book.")
        return game

    def undo_last_round(self, game_id: int) -> Game:
        with self._transaction() as conn:
            game_row, _players = self._locked_game(conn, game_id)
            last = self._last_round_row(conn, game_id, missing="There is no round to undo.")
            conn.execute("DELETE FROM round_entries WHERE round_id = ?", (last["id"],))
            conn.execute("DELETE FROM rounds WHERE id = ?", (last["id"],))
            self._apply_status(conn, game_id, game_row["target_score"])
        game = self.get_game(game_id)
        if game is None:
            raise NotFoundError("That game isn't in the score book.")
        return game

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def _transaction(self):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            conn.close()

    def _locked_game(self, conn: sqlite3.Connection, game_id: int) -> tuple[sqlite3.Row, list[Player]]:
        game_row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if game_row is None:
            raise NotFoundError("That game isn't in the score book.")
        players = [
            Player(id=row["id"], name=row["name"], seat=row["seat"])
            for row in conn.execute(
                "SELECT id, name, seat FROM players WHERE game_id = ? ORDER BY seat",
                (game_id,),
            )
        ]
        return game_row, players

    def _last_round_row(self, conn: sqlite3.Connection, game_id: int, *, missing: str) -> sqlite3.Row:
        last = conn.execute(
            """
            SELECT id, round_number
            FROM rounds
            WHERE game_id = ?
            ORDER BY round_number DESC
            LIMIT 1
            """,
            (game_id,),
        ).fetchone()
        if last is None:
            raise ScoringError(missing)
        return last

    def _score_counts(
        self,
        players: list[Player],
        counts: Mapping[int, tuple[int, int]],
    ) -> list[tuple[int, int, int, int]]:
        expected = {player.id for player in players}
        if set(counts) != expected:
            raise ScoringError("Enter Dutch and Blitz counts for every player.")
        scored = []
        for player in players:
            dutch, blitz = counts[player.id]
            scored.append((player.id, dutch, blitz, round_score(dutch, blitz)))
        return scored

    def _apply_status(self, conn: sqlite3.Connection, game_id: int, target_score: int) -> None:
        rows = conn.execute(
            """
            SELECT p.name AS name, COALESCE(SUM(e.score), 0) AS total
            FROM players p
            LEFT JOIN round_entries e ON e.player_id = p.id
            WHERE p.game_id = ?
            GROUP BY p.id
            ORDER BY p.seat
            """,
            (game_id,),
        ).fetchall()
        totals = {row["name"]: int(row["total"]) for row in rows}
        status = "finished" if outcome(totals, int(target_score)).finished else "active"
        conn.execute("UPDATE games SET status = ? WHERE id = ?", (status, game_id))

    def _load(self, conn: sqlite3.Connection, game_id: int) -> Game | None:
        game_row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if game_row is None:
            return None
        players = [
            Player(id=row["id"], name=row["name"], seat=row["seat"])
            for row in conn.execute(
                "SELECT id, name, seat FROM players WHERE game_id = ? ORDER BY seat",
                (game_id,),
            )
        ]
        seat_of = {player.id: player.seat for player in players}
        round_rows = conn.execute(
            """
            SELECT id, round_number
            FROM rounds
            WHERE game_id = ?
            ORDER BY round_number
            """,
            (game_id,),
        ).fetchall()
        entry_rows = conn.execute(
            """
            SELECT e.round_id, e.player_id, e.dutch_cards, e.blitz_left, e.score
            FROM round_entries e
            JOIN rounds r ON r.id = e.round_id
            WHERE r.game_id = ?
            """,
            (game_id,),
        ).fetchall()
        grouped: dict[int, list[RoundEntry]] = {row["id"]: [] for row in round_rows}
        for row in entry_rows:
            grouped[row["round_id"]].append(
                RoundEntry(
                    player_id=row["player_id"],
                    dutch_cards=row["dutch_cards"],
                    blitz_left=row["blitz_left"],
                    score=row["score"],
                )
            )
        rounds = [
            Round(
                number=row["round_number"],
                entries=sorted(grouped[row["id"]], key=lambda entry: seat_of[entry.player_id]),
            )
            for row in round_rows
        ]
        return Game(
            id=game_row["id"],
            target_score=game_row["target_score"],
            status=game_row["status"],
            created_at=game_row["created_at"],
            players=players,
            rounds=rounds,
        )
