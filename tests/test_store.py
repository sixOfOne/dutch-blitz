"""Persistence: saved rounds, the target, undo, and editing the last round."""

import sqlite3

import pytest

from app.errors import NotFoundError, ScoringError
from app.store import GameStore
from app.views import present


def _store(tmp_path) -> GameStore:
    store = GameStore(str(tmp_path / "games.sqlite"))
    store.init()
    return store


def _ids(store: GameStore, game_id: int) -> dict[str, int]:
    game = store.get_game(game_id)
    assert game is not None
    return {player.name: player.id for player in game.players}


def test_history_survives_reopening_the_database(tmp_path):
    path = tmp_path / "nested" / "games.sqlite"
    store = GameStore(str(path))
    store.init()
    game_id = store.create_game(["Ada", "Bo"])
    ids = _ids(store, game_id)
    store.record_round(game_id, {ids["Ada"]: (0, 10), ids["Bo"]: (5, 0)})

    reopened = GameStore(str(path))
    reopened.init()
    game = reopened.get_game(game_id)
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
    game_id = store.create_game(["Ada", "Bo"], 10)
    ids = _ids(store, game_id)
    game = store.record_round(game_id, {ids["Ada"]: (4, 3), ids["Bo"]: (1, 2)})
    assert game.status == "active"
    assert [entry.score for entry in game.rounds[0].entries] == [-2, -3]

    game = store.record_round(game_id, {ids["Ada"]: (12, 0), ids["Bo"]: (13, 0)})
    assert game.status == "finished"
    assert [round_.number for round_ in game.rounds] == [1, 2]
    view = present(game)
    assert view.winners == ["Ada", "Bo"]
    assert len(game.rounds[-1].entries) == 2


def test_target_override_is_what_ends_the_game(tmp_path):
    store = _store(tmp_path)
    game_id = store.create_game(["Ada", "Bo"], 100)
    ids = _ids(store, game_id)
    game = store.record_round(game_id, {ids["Ada"]: (40, 0), ids["Bo"]: (0, 0)})
    assert game.target_score == 100
    assert game.status == "active"
    game = store.record_round(game_id, {ids["Ada"]: (40, 0), ids["Bo"]: (0, 0)})
    assert game.status == "active"
    assert present(game).players[0].total == 80
    game = store.record_round(game_id, {ids["Ada"]: (20, 0), ids["Bo"]: (40, 0)})
    assert game.status == "finished"
    assert present(game).winners == ["Ada"]


def test_eight_players_can_score_and_one_or_nine_cannot(tmp_path):
    store = _store(tmp_path)
    eight = [f"P{index}" for index in range(1, 9)]
    game_id = store.create_game(eight, 75)
    game = store.get_game(game_id)
    assert game is not None
    assert [player.name for player in game.players] == eight
    assert [player.seat for player in game.players] == list(range(1, 9))
    store.record_round(game_id, {player.id: (index, 0) for index, player in enumerate(game.players)})
    assert store.get_game(game_id).status == "active"

    with pytest.raises(ScoringError, match="2 to 8"):
        store.create_game([f"P{index}" for index in range(1, 10)], 75)
    with pytest.raises(ScoringError, match="2 to 8"):
        store.create_game(["Solo"], 75)
    assert len(store.list_games()) == 1


def test_finishing_round_still_stores_the_other_players(tmp_path):
    store = _store(tmp_path)
    game_id = store.create_game(["Ada", "Bo"], 10)
    ids = _ids(store, game_id)
    game = store.record_round(game_id, {ids["Ada"]: (12, 0), ids["Bo"]: (1, 0)})
    assert game.status == "finished"
    assert [entry.score for entry in game.rounds[0].entries] == [12, 1]
    assert present(game).winners == ["Ada"]
    with pytest.raises(ScoringError, match="finished"):
        store.record_round(game_id, {ids["Ada"]: (1, 0), ids["Bo"]: (1, 0)})
    assert len(store.get_game(game_id).rounds) == 1


def test_partial_round_is_rejected(tmp_path):
    store = _store(tmp_path)
    game_id = store.create_game(["Ada", "Bo"], 75)
    ids = _ids(store, game_id)
    with pytest.raises(ScoringError, match="every player"):
        store.record_round(game_id, {ids["Ada"]: (1, 0)})
    assert store.get_game(game_id).rounds == []


def test_undo_reopens_a_finished_game_and_round_numbers_continue(tmp_path):
    store = _store(tmp_path)
    game_id = store.create_game(["Ada", "Bo"], 10)
    ids = _ids(store, game_id)
    store.record_round(game_id, {ids["Ada"]: (1, 0), ids["Bo"]: (1, 0)})
    store.record_round(game_id, {ids["Ada"]: (12, 0), ids["Bo"]: (0, 0)})
    assert store.get_game(game_id).status == "finished"

    game = store.undo_last_round(game_id)
    assert game.status == "active"
    assert [round_.number for round_ in game.rounds] == [1]
    game = store.record_round(game_id, {ids["Ada"]: (2, 0), ids["Bo"]: (2, 0)})
    assert [round_.number for round_ in game.rounds] == [1, 2]
    assert game.status == "active"

    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        assert conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM round_entries").fetchone()[0] == 4

    store.undo_last_round(game_id)
    store.undo_last_round(game_id)
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM round_entries").fetchone()[0] == 0
    with pytest.raises(ScoringError, match="no round"):
        store.undo_last_round(game_id)


def test_edit_last_round_can_reopen_or_finish_the_game(tmp_path):
    store = _store(tmp_path)
    game_id = store.create_game(["Ada", "Bo"], 10)
    ids = _ids(store, game_id)
    store.record_round(game_id, {ids["Ada"]: (12, 0), ids["Bo"]: (0, 0)})
    assert store.get_game(game_id).status == "finished"

    game = store.replace_last_round(game_id, {ids["Ada"]: (3, 0), ids["Bo"]: (0, 0)})
    assert game.status == "active"
    assert game.rounds[0].number == 1
    assert [entry.score for entry in game.rounds[0].entries] == [3, 0]
    assert present(game).headline == "Ada leads with 3"

    game = store.replace_last_round(game_id, {ids["Ada"]: (12, 0), ids["Bo"]: (12, 0)})
    assert game.status == "finished"
    assert present(game).winners == ["Ada", "Bo"]
    other_id = store.create_game(["Cy", "Di"], 75)
    other_ids = _ids(store, other_id)
    with pytest.raises(ScoringError, match="no round to fix"):
        store.replace_last_round(other_id, {other_ids["Cy"]: (0, 0), other_ids["Di"]: (0, 0)})


def test_missing_game(tmp_path):
    store = _store(tmp_path)
    assert store.get_game(999) is None
    with pytest.raises(NotFoundError):
        store.record_round(999, {})
    with pytest.raises(NotFoundError):
        store.undo_last_round(999)
    with pytest.raises(ScoringError, match="1 to 999"):
        store.create_game(["Ada", "Bo"], 0)
