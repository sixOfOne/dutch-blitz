"""SQLite persistence for games, players, and rounds."""

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import secrets
import sqlite3

from app.errors import ForbiddenError, NotFoundError, ScoringError
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
    status TEXT NOT NULL CHECK (status IN ('active', 'finished')),
    owner_id TEXT,
    invite_token TEXT
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
    seat INTEGER NOT NULL CHECK (seat BETWEEN 1 AND 8),
    user_id TEXT,
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
    user_id: str | None = None


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
    owner_id: str | None = None
    invite_token: str | None = None


@dataclass
class SeatInvite:
    player_id: int
    name: str
    seat: int
    open: bool


@dataclass
class InvitePreview:
    """What a signed-in person sees before they claim a seat. No token."""

    game_id: int
    target_score: int
    seats: list[SeatInvite]
    already_joined: bool
    is_owner: bool


MAX_OWNER_ID_LENGTH = 200


def require_owner_id(owner_id: str) -> str:
    """Reject a missing owner before a game is written or read for that account."""
    if not isinstance(owner_id, str):
        raise ValueError("owner_id is required")
    cleaned = owner_id.strip()
    if not cleaned or len(cleaned) > MAX_OWNER_ID_LENGTH:
        raise ValueError("owner_id is required")
    return cleaned


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
            self._migrate(conn)

    def create_game(
        self,
        names: list[str],
        target_score: int = DEFAULT_TARGET,
        *,
        owner_id: str,
    ) -> int:
        owner = require_owner_id(owner_id)
        cleaned = validate_players(names)
        target = validate_target(target_score)
        with self._transaction() as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO games (target_score, created_at, status, owner_id)
                    VALUES (?, ?, 'active', ?)
                    """,
                    (target, utc_now(), owner),
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

    def get_game(self, game_id: int, owner_id: str) -> Game | None:
        """Return the game when this account started it or has claimed a seat."""
        user = require_owner_id(owner_id)
        with self._connect() as conn:
            game = self._load(conn, game_id)
        if game is None or not _can_view(game, user):
            return None
        return game

    def list_games(self, owner_id: str) -> list[Game]:
        """Games this account started, plus games where they claimed a seat."""
        user = require_owner_id(owner_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id FROM games
                WHERE owner_id = ?
                   OR id IN (SELECT game_id FROM players WHERE user_id = ?)
                ORDER BY id DESC
                """,
                (user, user),
            ).fetchall()
            loaded = [self._load(conn, row["id"]) for row in rows]
            return [game for game in loaded if game is not None and _can_view(game, user)]

    def set_invite(self, game_id: int, *, owner_id: str, mode: str) -> str | None:
        """Create, rotate, or disable the invite link. Owner only.

        Creating again keeps the current link. Rotating replaces it, so the
        old link stops working. Disabling clears it. Claimed seats stay either way.
        """
        if mode not in {"create", "rotate", "disable"}:
            raise ValueError("mode must be create, rotate, or disable")
        owner = require_owner_id(owner_id)
        with self._transaction() as conn:
            self._require_owner_game(conn, game_id, owner)
            if mode == "disable":
                conn.execute("UPDATE games SET invite_token = NULL WHERE id = ?", (game_id,))
                return None
            current = conn.execute(
                "SELECT invite_token FROM games WHERE id = ?",
                (game_id,),
            ).fetchone()
            if mode == "create" and current["invite_token"]:
                return str(current["invite_token"])
            token = secrets.token_urlsafe(18)
            conn.execute(
                "UPDATE games SET invite_token = ? WHERE id = ?",
                (token, game_id),
            )
        return token

    def revoke_seat(self, game_id: int, player_id: int, *, owner_id: str) -> None:
        """Unlink the account on a seat. The seat can be claimed again. Owner only."""
        owner = require_owner_id(owner_id)
        with self._transaction() as conn:
            _game_row, players = self._require_owner_game(conn, game_id, owner)
            if not any(player.id == player_id for player in players):
                raise ScoringError("That seat isn't in this game.")
            conn.execute(
                "UPDATE players SET user_id = NULL WHERE id = ? AND game_id = ?",
                (player_id, game_id),
            )

    def claim_own_seat(self, game_id: int, player_id: int, *, owner_id: str) -> None:
        """The owner marks one open seat as their own. Sharing can be off."""
        owner = require_owner_id(owner_id)
        with self._transaction() as conn:
            self._require_owner_game(conn, game_id, owner)
            self._claim(conn, game_id, player_id, owner)

    def describe_invite(self, token: str, *, user_id: str) -> InvitePreview | None:
        user = require_owner_id(user_id)
        if not _valid_token(token):
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, owner_id, target_score
                FROM games
                WHERE invite_token = ?
                """,
                (token,),
            ).fetchone()
            if row is None or row["owner_id"] is None:
                return None
            players = self._players(conn, int(row["id"]))
        return InvitePreview(
            game_id=int(row["id"]),
            target_score=int(row["target_score"]),
            seats=[
                SeatInvite(
                    player_id=player.id,
                    name=player.name,
                    seat=player.seat,
                    open=player.user_id is None,
                )
                for player in players
            ],
            already_joined=any(player.user_id == user for player in players),
            is_owner=row["owner_id"] == user,
        )

    def join_game(self, token: str, player_id: int, *, user_id: str) -> int:
        """Claim an open seat through the current invite link."""
        user = require_owner_id(user_id)
        if not _valid_token(token):
            raise NotFoundError("That invite link isn't active.")
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT id, owner_id FROM games WHERE invite_token = ?",
                (token,),
            ).fetchone()
            if row is None or row["owner_id"] is None:
                raise NotFoundError("That invite link isn't active.")
            game_id = int(row["id"])
            self._claim(conn, game_id, player_id, user)
        return game_id

    def record_round(
        self,
        game_id: int,
        counts: Mapping[int, tuple[int, int]],
        *,
        owner_id: str,
    ) -> Game:
        """Append a round. The owner or anyone who claimed a seat may do this."""
        user = require_owner_id(owner_id)
        with self._transaction() as conn:
            game_row, players, _role = self._access(conn, game_id, user)
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
        game = self.get_game(game_id, user)
        if game is None:
            raise NotFoundError("That game isn't in the score book.")
        return game

    def replace_last_round(
        self,
        game_id: int,
        counts: Mapping[int, tuple[int, int]],
        *,
        owner_id: str,
    ) -> Game:
        """Replace the card counts on the last round and recalculate the result.

        Editing can finish a game or reopen one, because the target is checked
        again from the corrected totals.
        """
        user = require_owner_id(owner_id)
        with self._transaction() as conn:
            game_row, players = self._require_owner_game(conn, game_id, user)
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
        game = self.get_game(game_id, user)
        if game is None:
            raise NotFoundError("That game isn't in the score book.")
        return game

    def undo_last_round(self, game_id: int, *, owner_id: str) -> Game:
        user = require_owner_id(owner_id)
        with self._transaction() as conn:
            game_row, _players = self._require_owner_game(conn, game_id, user)
            last = self._last_round_row(conn, game_id, missing="There is no round to undo.")
            conn.execute("DELETE FROM round_entries WHERE round_id = ?", (last["id"],))
            conn.execute("DELETE FROM rounds WHERE id = ?", (last["id"],))
            self._apply_status(conn, game_id, game_row["target_score"])
        game = self.get_game(game_id, user)
        if game is None:
            raise NotFoundError("That game isn't in the score book.")
        return game

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add account and sharing columns without rewriting old rows.

        Games saved before accounts keep a NULL owner. They stay in the file,
        are hidden from every account, and cannot be claimed or invited.
        Games saved before sharing keep a NULL invite token and unclaimed seats.
        """
        game_columns = {row["name"] for row in conn.execute("PRAGMA table_info(games)")}
        if "owner_id" not in game_columns:
            conn.execute("ALTER TABLE games ADD COLUMN owner_id TEXT")
        if "invite_token" not in game_columns:
            conn.execute("ALTER TABLE games ADD COLUMN invite_token TEXT")
        player_columns = {row["name"] for row in conn.execute("PRAGMA table_info(players)")}
        if "user_id" not in player_columns:
            conn.execute("ALTER TABLE players ADD COLUMN user_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_games_owner ON games(owner_id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_games_invite ON games(invite_token)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_players_user ON players(user_id)")

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

    def _access(
        self,
        conn: sqlite3.Connection,
        game_id: int,
        user_id: str,
    ) -> tuple[sqlite3.Row, list[Player], str]:
        game_row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if game_row is None or game_row["owner_id"] is None:
            raise NotFoundError("That game isn't in the score book.")
        players = self._players(conn, game_id)
        if game_row["owner_id"] == user_id:
            return game_row, players, "owner"
        if any(player.user_id == user_id for player in players):
            return game_row, players, "participant"
        raise NotFoundError("That game isn't in the score book.")

    def _require_owner_game(
        self,
        conn: sqlite3.Connection,
        game_id: int,
        user_id: str,
    ) -> tuple[sqlite3.Row, list[Player]]:
        game_row, players, role = self._access(conn, game_id, user_id)
        if role != "owner":
            raise ForbiddenError("Only the person who started this game can do that.")
        return game_row, players

    def _claim(self, conn: sqlite3.Connection, game_id: int, player_id: int, user_id: str) -> None:
        existing = conn.execute(
            "SELECT id FROM players WHERE game_id = ? AND user_id = ?",
            (game_id, user_id),
        ).fetchone()
        if existing is not None:
            raise ScoringError("You already have a seat in this game.")
        seat = conn.execute(
            "SELECT id, user_id FROM players WHERE id = ? AND game_id = ?",
            (player_id, game_id),
        ).fetchone()
        if seat is None:
            raise ScoringError("That seat isn't in this game.")
        if seat["user_id"] is not None:
            raise ScoringError("That seat is already taken.")
        updated = conn.execute(
            """
            UPDATE players
            SET user_id = ?
            WHERE id = ? AND game_id = ? AND user_id IS NULL
            """,
            (user_id, player_id, game_id),
        )
        if updated.rowcount != 1:
            raise ScoringError("That seat is already taken.")

    def _players(self, conn: sqlite3.Connection, game_id: int) -> list[Player]:
        return [
            Player(id=row["id"], name=row["name"], seat=row["seat"], user_id=row["user_id"])
            for row in conn.execute(
                "SELECT id, name, seat, user_id FROM players WHERE game_id = ? ORDER BY seat",
                (game_id,),
            )
        ]

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
        players = self._players(conn, game_id)
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
            owner_id=game_row["owner_id"],
            invite_token=game_row["invite_token"],
        )


def _can_view(game: Game, user_id: str) -> bool:
    if game.owner_id is None:
        return False
    if game.owner_id == user_id:
        return True
    return any(player.user_id == user_id for player in game.players)


def _valid_token(token: str) -> bool:
    if not isinstance(token, str) or not 20 <= len(token) <= 80:
        return False
    return all(character.isalnum() or character in "_-" for character in token)
