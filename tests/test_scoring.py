"""Unit tests for round scores, running totals, and when a game ends."""

import pytest

from app.errors import ScoringError
from app.scoring import (
    DEFAULT_TARGET,
    MAX_PLAYERS,
    MIN_PLAYERS,
    add_round,
    outcome,
    parse_count,
    parse_target,
    round_score,
    validate_players,
    validate_target,
    winners,
)


def test_round_score_formula_and_negative_results():
    assert round_score(0, 0) == 0
    assert round_score(10, 0) == 10
    assert round_score(10, 5) == 0
    assert round_score(0, 1) == -2
    assert round_score(3, 10) == -17
    assert round_score(40, 10) == 20


@pytest.mark.parametrize(
    ("dutch", "blitz"),
    [(-1, 0), (0, -1), (41, 0), (0, 11), (True, 0), (1.5, 0)],
)
def test_round_score_rejects_impossible_card_counts(dutch, blitz):
    with pytest.raises(ScoringError):
        round_score(dutch, blitz)


def test_parse_count_accepts_digits_and_rejects_junk():
    assert parse_count("40", label="Dutch cards", maximum=40) == 40
    assert parse_count(" 0 ", label="Blitz cards left", maximum=10) == 0
    with pytest.raises(ScoringError):
        parse_count("41", label="Dutch cards", maximum=40)
    with pytest.raises(ScoringError):
        parse_count("11", label="Blitz cards left", maximum=10)
    with pytest.raises(ScoringError):
        parse_count("-1", label="Dutch cards", maximum=40)
    with pytest.raises(ScoringError):
        parse_count("4.5", label="Dutch cards", maximum=40)
    with pytest.raises(ScoringError):
        parse_count("", label="Dutch cards", maximum=40)


def test_negative_round_lowers_the_running_total_without_ending_the_game():
    totals = add_round({"Ada": 20, "Bo": 18}, {"Ada": round_score(2, 4), "Bo": round_score(8, 2)})
    assert totals == {"Ada": 14, "Bo": 22}
    assert outcome(totals, DEFAULT_TARGET).finished is False
    assert outcome(totals, DEFAULT_TARGET).winners == ()
    assert outcome(totals, DEFAULT_TARGET).leaders == ("Bo",)


def test_running_totals_keep_seat_order_and_require_every_player():
    totals = add_round({"Cy": 0, "Ada": 0}, {"Cy": 1, "Ada": 2})
    assert list(totals) == ["Cy", "Ada"]
    with pytest.raises(ScoringError, match="every player"):
        add_round({"Ada": 0, "Bo": 0}, {"Ada": 1})
    with pytest.raises(ScoringError):
        add_round({"Ada": 0}, {"Ada": True})


def test_game_ends_on_the_target_and_when_a_total_passes_it():
    assert outcome({"Ada": 74, "Bo": 10}, 75).finished is False
    assert winners({"Ada": 75, "Bo": 10}, 75) == ("Ada",)
    assert winners({"Ada": 90, "Bo": 80}, 75) == ("Ada",)


def test_target_override_changes_when_the_game_ends():
    totals = {"Ada": 80, "Bo": 70}
    assert DEFAULT_TARGET == 75
    assert winners(totals, DEFAULT_TARGET) == ("Ada",)
    assert outcome(totals, 100).finished is False
    assert winners(totals, 100) == ()
    assert winners(totals, 70) == ("Ada",)
    assert validate_target(1) == 1
    assert winners({"Ada": 1, "Bo": 0}, 1) == ("Ada",)


@pytest.mark.parametrize("target", [0, -5, 1000, True, 1.5])
def test_target_bounds(target):
    with pytest.raises(ScoringError):
        validate_target(target)
    if isinstance(target, str):
        with pytest.raises(ScoringError):
            parse_target(target)


def test_parse_target():
    assert parse_target("75") == 75
    assert parse_target(" 100 ") == 100
    with pytest.raises(ScoringError):
        parse_target("0")
    with pytest.raises(ScoringError):
        parse_target("1000")
    with pytest.raises(ScoringError):
        parse_target("75.5")


def test_tie_shares_the_win_and_ends_the_game():
    result = outcome({"Ada": 75, "Bo": 75, "Cy": 60}, 75)
    assert result.finished is True
    assert result.winners == ("Ada", "Bo")
    assert result.leaders == ("Ada", "Bo")


def test_winner_order_follows_seat_order_not_alphabetical_order():
    assert winners({"Cy": 80, "Ada": 80, "Bo": 10}, 75) == ("Cy", "Ada")


def test_highest_total_wins_when_several_players_reach_the_target():
    assert winners({"Ada": 81, "Bo": 76, "Cy": 90}, 75) == ("Cy",)


def test_a_negative_leader_is_not_a_winner():
    result = outcome({"Ada": -2, "Bo": -5}, 75)
    assert result.finished is False
    assert result.leaders == ("Ada",)
    assert result.winners == ()
    tied = outcome({"Ada": -2, "Bo": -2}, 75)
    assert tied.leaders == ("Ada", "Bo")
    assert tied.finished is False


def test_player_limits_accept_two_and_eight_and_reject_the_rest():
    assert MIN_PLAYERS == 2
    assert MAX_PLAYERS == 8
    assert validate_players(["Ada", "Bo"]) == ["Ada", "Bo"]
    eight = [f"Player {index}" for index in range(1, 9)]
    assert validate_players(eight) == eight
    assert validate_players(["Ada", "Bo", " ", ""]) == ["Ada", "Bo"]
    assert validate_players([" Ada  Lovelace ", "Bo"]) == ["Ada Lovelace", "Bo"]

    with pytest.raises(ScoringError, match="2 to 8"):
        validate_players(["Only"])
    with pytest.raises(ScoringError, match="2 to 8"):
        validate_players([])
    with pytest.raises(ScoringError, match="2 to 8"):
        validate_players(eight + ["Extra"])
    with pytest.raises(ScoringError, match="blank at the end"):
        validate_players(["Ada", " ", "Bo"])
    with pytest.raises(ScoringError, match="listed twice"):
        validate_players(["Ada", "ada"])
    with pytest.raises(ScoringError, match="listed twice"):
        validate_players(["Ann Marie", "ann  marie"])
    with pytest.raises(ScoringError, match="40"):
        validate_players(["A" * 41, "Bo"])
    assert validate_players(["A" * 40, "Bo"]) == ["A" * 40, "Bo"]
    with pytest.raises(ScoringError, match="2 to 8"):
        validate_players("Ada")
