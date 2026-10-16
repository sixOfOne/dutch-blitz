"""Persistence: saved rounds, the target, undo, and editing the last round."""

import sqlite3
from pathlib import Path

import pytest

from app.errors import NotFoundError, ScoringError
from app.store import Game, GameStore
from app.views import present

OWNER = "user_owner"


def _store(tmp_path: Path) -> GameStore:
    store = GameStore(str(tmp_path / "games.sqlite"))
    store.init()
    return store


def _open(store: GameStore, game_id: int, owner: str = OWNER) -> Game:
    game = store.get_game(game_id, owner)
    assert game is not None
    return game


def _ids(store: GameStore, game_id: int) -> dict[str, int]:
    game = store.get_game(game_id, OWNER)
    assert game is not None
    return {player.name: player.id for player in game.players}


def test_history_survives_reopening_the_database(tmp_path):
    path = tmp_path / "nested" / "games.sqlite"
    store = GameStore(str(path))
    store.init()
    game_id = store.create_game(["Ada", "Bo"], owner_id=OWNER)
    ids = _ids(store, game_id)
    store.record_round(game_id, {ids["Ada"]: (0, 10), ids["Bo"]: (5, 0)}, owner_id=OWNER)

    reopened = GameStore(str(path))
    reopened.init()
    game = reopened.get_game(game_id, OWNER)
    assert game is not None
    assert game.target_score == 75
    assert game.status == "active"
    assert path.exists()
    scores = {entry.player_id: entry.score for entry in game.rounds[0].entries}
    assert scores[ids["Ada"]] == -20
    assert scores[ids["Bo"]] == 5
    view = present(game)
    assert [player.total for player in view.players] == [-20, 5]


def test_negative_round_does_not_finish_and_a_later_round_can(tmp_path):
    store = _store(tmp_path)
    game_id = store.create_game(["Ada", "Bo"], 10, owner_id=OWNER)
    ids = _ids(store, game_id)
    game = store.record_round(game_id, {ids["Ada"]: (4, 3), ids["Bo"]: (1, 2)}, owner_id=OWNER)
    assert game.status == "active"
    assert [entry.score for entry in game.rounds[0].entries] == [-2, -3]

    game = store.record_round(game_id, {ids["Ada"]: (12, 0), ids["Bo"]: (13, 0)}, owner_id=OWNER)
    assert game.status == "finished"
    assert [round_.number for round_ in game.rounds] == [1, 2]
    view = present(game)
    assert view.winners == ["Ada", "Bo"]
    assert len(game.rounds[-1].entries) == 2


def test_target_override_is_what_ends_the_game(tmp_path):
    store = _store(tmp_path)
    game_id = store.create_game(["Ada", "Bo"], 100, owner_id=OWNER)
    ids = _ids(store, game_id)
    game = store.record_round(game_id, {ids["Ada"]: (40, 0), ids["Bo"]: (0, 0)}, owner_id=OWNER)
    assert game.target_score == 100
    assert game.status == "active"
    game = store.record_round(game_id, {ids["Ada"]: (40, 0), ids["Bo"]: (0, 0)}, owner_id=OWNER)
    assert game.status == "active"
    assert present(game).players[0].total == 80
    game = store.record_round(game_id, {ids["Ada"]: (20, 0), ids["Bo"]: (40, 0)}, owner_id=OWNER)
    assert game.status == "finished"
    assert present(game).winners == ["Ada"]


def test_eight_players_can_score_and_one_or_nine_cannot(tmp_path):
    store = _store(tmp_path)
    eight = [f"P{index}" for index in range(1, 9)]
    game_id = store.create_game(eight, 75, owner_id=OWNER)
    game = store.get_game(game_id, OWNER)
    assert game is not None
    assert [player.name for player in game.players] == eight
    assert [player.seat for player in game.players] == list(range(1, 9))
    store.record_round(
        game_id,
        {player.id: (index, 0) for index, player in enumerate(game.players)},
        owner_id=OWNER,
    )
    assert _open(store, game_id).status == "active"

    with pytest.raises(ScoringError, match="2 to 8"):
        store.create_game([f"P{index}" for index in range(1, 10)], 75, owner_id=OWNER)
    with pytest.raises(ScoringError, match="2 to 8"):
        store.create_game(["Solo"], 75, owner_id=OWNER)
    assert len(store.list_games(OWNER)) == 1


def test_finishing_round_still_stores_the_other_players(tmp_path):
    store = _store(tmp_path)
    game_id = store.create_game(["Ada", "Bo"], 10, owner_id=OWNER)
    ids = _ids(store, game_id)
    game = store.record_round(game_id, {ids["Ada"]: (12, 0), ids["Bo"]: (1, 0)}, owner_id=OWNER)
    assert game.status == "finished"
    assert [entry.score for entry in game.rounds[0].entries] == [12, 1]
    assert present(game).winners == ["Ada"]
    with pytest.raises(ScoringError, match="finished"):
        store.record_round(game_id, {ids["Ada"]: (1, 0), ids["Bo"]: (1, 0)}, owner_id=OWNER)
    assert len(_open(store, game_id).rounds) == 1


def test_partial_round_is_rejected(tmp_path):
    store = _store(tmp_path)
    game_id = store.create_game(["Ada", "Bo"], 75, owner_id=OWNER)
    ids = _ids(store, game_id)
    with pytest.raises(ScoringError, match="every player"):
        store.record_round(game_id, {ids["Ada"]: (1, 0)}, owner_id=OWNER)
    assert _open(store, game_id).rounds == []


def test_undo_reopens_a_finished_game_and_round_numbers_continue(tmp_path):
    store = _store(tmp_path)
    game_id = store.create_game(["Ada", "Bo"], 10, owner_id=OWNER)
    ids = _ids(store, game_id)
    store.record_round(game_id, {ids["Ada"]: (1, 0), ids["Bo"]: (1, 0)}, owner_id=OWNER)
    store.record_round(game_id, {ids["Ada"]: (12, 0), ids["Bo"]: (0, 0)}, owner_id=OWNER)
    assert _open(store, game_id).status == "finished"

    game = store.undo_last_round(game_id, owner_id=OWNER)
    assert game.status == "active"
    assert [round_.number for round_ in game.rounds] == [1]
    game = store.record_round(game_id, {ids["Ada"]: (2, 0), ids["Bo"]: (2, 0)}, owner_id=OWNER)
    assert [round_.number for round_ in game.rounds] == [1, 2]
    assert game.status == "active"

    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        assert conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM round_entries").fetchone()[0] == 4

    store.undo_last_round(game_id, owner_id=OWNER)
    store.undo_last_round(game_id, owner_id=OWNER)
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM round_entries").fetchone()[0] == 0
    with pytest.raises(ScoringError, match="no round"):
        store.undo_last_round(game_id, owner_id=OWNER)


def test_edit_last_round_can_reopen_or_finish_the_game(tmp_path):
    store = _store(tmp_path)
    game_id = store.create_game(["Ada", "Bo"], 10, owner_id=OWNER)
    ids = _ids(store, game_id)
    store.record_round(game_id, {ids["Ada"]: (12, 0), ids["Bo"]: (0, 0)}, owner_id=OWNER)
    assert _open(store, game_id).status == "finished"

    game = store.replace_last_round(
        game_id, {ids["Ada"]: (3, 0), ids["Bo"]: (0, 0)}, owner_id=OWNER
    )
    assert game.status == "active"
    assert game.rounds[0].number == 1
    assert [entry.score for entry in game.rounds[0].entries] == [3, 0]
    assert present(game).headline == "Ada leads with 3"

    game = store.replace_last_round(
        game_id, {ids["Ada"]: (12, 0), ids["Bo"]: (12, 0)}, owner_id=OWNER
    )
    assert game.status == "finished"
    assert present(game).winners == ["Ada", "Bo"]
    other_id = store.create_game(["Cy", "Di"], 75, owner_id=OWNER)
    other_ids = _ids(store, other_id)
    with pytest.raises(ScoringError, match="no round to fix"):
        store.replace_last_round(
            other_id, {other_ids["Cy"]: (0, 0), other_ids["Di"]: (0, 0)}, owner_id=OWNER
        )


def test_missing_game(tmp_path):
    store = _store(tmp_path)
    assert store.get_game(999, OWNER) is None
    with pytest.raises(NotFoundError):
        store.record_round(999, {}, owner_id=OWNER)
    with pytest.raises(NotFoundError):
        store.undo_last_round(999, owner_id=OWNER)
    with pytest.raises(ScoringError, match="1 to 999"):
        store.create_game(["Ada", "Bo"], 0, owner_id=OWNER)


def test_another_owner_cannot_read_or_change_a_game(tmp_path):
    store = _store(tmp_path)
    game_id = store.create_game(["Ada", "Bo"], 10, owner_id="user_a")
    ids = {player.name: player.id for player in _open(store, game_id, "user_a").players}
    store.record_round(game_id, {ids["Ada"]: (4, 0), ids["Bo"]: (1, 0)}, owner_id="user_a")

    assert store.list_games("user_b") == []
    assert store.get_game(game_id, "user_b") is None
    with pytest.raises(NotFoundError):
        store.record_round(game_id, {ids["Ada"]: (1, 0), ids["Bo"]: (1, 0)}, owner_id="user_b")
    with pytest.raises(NotFoundError):
        store.replace_last_round(
            game_id, {ids["Ada"]: (1, 0), ids["Bo"]: (1, 0)}, owner_id="user_b"
        )
    with pytest.raises(NotFoundError):
        store.undo_last_round(game_id, owner_id="user_b")

    game = _open(store, game_id, "user_a")
    assert game.owner_id == "user_a"
    assert len(game.rounds) == 1
    assert [entry.score for entry in game.rounds[0].entries] == [4, 1]


def test_legacy_games_stay_unowned_and_hidden(tmp_path):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE games (
                id INTEGER PRIMARY KEY,
                target_score INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE players (
                id INTEGER PRIMARY KEY,
                game_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                seat INTEGER NOT NULL
            );
            INSERT INTO games (target_score, created_at, status)
            VALUES (75, '2020-01-01T00:00:00+00:00', 'active');
            INSERT INTO players (game_id, name, seat) VALUES (1, 'Ada', 1), (1, 'Bo', 2);
            """
        )
    store = GameStore(str(path))
    store.init()
    assert store.list_games(OWNER) == []
    assert store.get_game(1, OWNER) is None
    with pytest.raises(NotFoundError):
        store.undo_last_round(1, owner_id=OWNER)
    with sqlite3.connect(path) as conn:
        owner = conn.execute("SELECT owner_id FROM games WHERE id = 1").fetchone()[0]
    assert owner is None
    fresh = store.create_game(["Cy", "Di"], owner_id=OWNER)
    assert store.get_game(fresh, OWNER) is not None
    assert store.get_game(1, OWNER) is None


def test_deck_color_and_icon_round_trip_and_default_icon(tmp_path):
    store = _store(tmp_path)
    game_id = store.create_game(
        ["Ada", "Bo", ""],
        owner_id=OWNER,
        colors=["red", "navy", "red"],
        icons=["wheelbarrow", None, "pump"],
    )
    game = store.get_game(game_id, OWNER)
    assert game is not None
    assert [(player.name, player.deck_color, player.deck_icon) for player in game.players] == [
        ("Ada", "red", "wheelbarrow"),
        ("Bo", "navy", "wheelbarrow"),
    ]
    store.init()
    again = store.get_game(game_id, OWNER)
    assert again is not None
    assert [(player.deck_color, player.deck_icon) for player in again.players] == [
        ("red", "wheelbarrow"),
        ("navy", "wheelbarrow"),
    ]


def test_duplicate_deck_color_is_rejected_and_not_saved(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ScoringError, match="Red is already chosen"):
        store.create_game(
            ["Ada", "Bo"],
            owner_id=OWNER,
            colors=["RED", "red"],
            icons=["carriage", "pump"],
        )
    assert store.list_games(OWNER) == []
    with pytest.raises(ScoringError, match="deck color"):
        store.create_game(["Ada", "Bo"], owner_id=OWNER, colors=["mauve", "blue"])
    with pytest.raises(ScoringError, match="deck icon"):
        store.create_game(
            ["Ada", "Bo"],
            owner_id=OWNER,
            colors=["red", "blue"],
            icons=["carriage", "unicorn"],
        )
    assert store.list_games(OWNER) == []


def test_saved_games_gain_deck_columns_without_losing_rows(tmp_path):
    path = tmp_path / "backup.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE games (
                id INTEGER PRIMARY KEY,
                target_score INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                owner_id TEXT,
                invite_token TEXT
            );
            CREATE TABLE players (
                id INTEGER PRIMARY KEY,
                game_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                seat INTEGER NOT NULL,
                user_id TEXT
            );
            CREATE TABLE rounds (
                id INTEGER PRIMARY KEY,
                game_id INTEGER NOT NULL,
                round_number INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE round_entries (
                id INTEGER PRIMARY KEY,
                round_id INTEGER NOT NULL,
                player_id INTEGER NOT NULL,
                dutch_cards INTEGER NOT NULL,
                blitz_left INTEGER NOT NULL,
                score INTEGER NOT NULL
            );
            INSERT INTO games (target_score, created_at, status, owner_id)
            VALUES (75, '2024-06-01T00:00:00+00:00', 'active', 'user_owner');
            INSERT INTO players (game_id, name, seat) VALUES (1, 'Ada', 1), (1, 'Bo', 2);
            INSERT INTO rounds (game_id, round_number, created_at)
            VALUES (1, 1, '2024-06-01T01:00:00+00:00');
            INSERT INTO round_entries (round_id, player_id, dutch_cards, blitz_left, score)
            VALUES (1, 1, 4, 1, 2), (1, 2, 3, 0, 3);
            """
        )
    store = GameStore(str(path))
    store.init()
    game = store.get_game(1, OWNER)
    assert game is not None
    assert [(player.name, player.deck_color, player.deck_icon) for player in game.players] == [
        ("Ada", None, None),
        ("Bo", None, None),
    ]
    assert [entry.score for entry in game.rounds[0].entries] == [2, 3]
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(players)")}
        stored = conn.execute("SELECT deck_color, deck_icon FROM players ORDER BY seat").fetchall()
    assert {"deck_color", "deck_icon"} <= columns
    assert stored == [(None, None), (None, None)]

    store.init()
    fresh = store.create_game(["Cy", "Di"], owner_id=OWNER, colors=["pink", "purple"])
    fresh_game = store.get_game(fresh, OWNER)
    assert fresh_game is not None
    assert [(player.deck_color, player.deck_icon) for player in fresh_game.players] == [
        ("pink", "water-can"),
        ("purple", "butter-churn"),
    ]
    original = store.get_game(1, OWNER)
    assert original is not None
    assert [player.deck_color for player in original.players] == [None, None]
    assert [entry.score for entry in original.rounds[0].entries] == [2, 3]


def test_owner_id_is_required(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="owner_id"):
        store.create_game(["Ada", "Bo"], owner_id="  ")
    with pytest.raises(ValueError, match="owner_id"):
        store.list_games("x" * 201)
