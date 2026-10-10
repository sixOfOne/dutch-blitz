"""Wording and running totals shown once a round has been scored."""

from app.store import Game, Player, Round, RoundEntry
from app.views import format_timestamp, present


def _game(totals: dict[str, int], target: int, *, rounds: int = 1) -> Game:
    players = [
        Player(id=index, name=name, seat=index)
        for index, name in enumerate(totals, start=1)
    ]
    played = []
    if rounds:
        played.append(
            Round(
                number=1,
                entries=[
                    RoundEntry(
                        player_id=player.id,
                        dutch_cards=0,
                        blitz_left=0,
                        score=totals[player.name],
                    )
                    for player in players
                ],
            )
        )
    return Game(
        id=1,
        target_score=target,
        status="active",
        created_at="2026-10-09T18:52:00+00:00",
        players=players,
        rounds=played,
    )


def test_not_started_copy():
    view = present(_game({"Ada": 0, "Bo": 0}, 75, rounds=0))
    assert view.headline == "First to 75"
    assert view.finished is False
    assert view.next_round == 1
    assert view.created_label == "Oct 9, 2026, 6:52 PM UTC"


def test_negative_leader_is_still_in_progress():
    view = present(_game({"Ada": -2, "Bo": -5}, 75))
    assert view.finished is False
    assert view.headline == "Ada leads with −2"
    assert [player.tag for player in view.players] == ["Lead", None]


def test_target_override_keeps_a_game_in_progress_past_seventy_five():
    view = present(_game({"Ada": 80, "Bo": 70}, 100))
    assert view.finished is False
    assert view.headline == "Ada leads with 80"
    assert view.subhead == "First to 100. Round 2 is next."


def test_sole_winner_is_the_highest_total():
    view = present(_game({"Bo": 9, "Ada": 12}, 10))
    assert view.winners == ["Ada"]
    assert view.headline == "Ada wins with 12"
    assert [player.tag for player in view.players] == [None, "Win"]


def test_tie_is_a_shared_win():
    view = present(_game({"Ada": 10, "Bo": 10, "Cy": 4}, 10))
    assert view.finished is True
    assert view.winners == ["Ada", "Bo"]
    assert view.headline == "Ada and Bo tie with 10"
    assert "No tiebreaker" in view.subhead
    assert [player.tag for player in view.players] == ["Win", "Win", None]


def test_three_way_tie_copy():
    view = present(_game({"Ada": 5, "Bo": 5, "Cy": 5}, 5))
    assert view.headline == "Ada, Bo, and Cy tie with 5"


def test_all_tied_under_the_target_marks_nobody():
    view = present(_game({"Ada": 4, "Bo": 4}, 75))
    assert view.headline == "All tied at 4"
    assert all(player.tag is None for player in view.players)


def test_score_sheet_keeps_running_totals_through_a_negative_round_and_a_tie():
    players = [Player(1, "Ada", 1), Player(2, "Bo", 2)]
    game = Game(
        id=1,
        target_score=10,
        status="active",
        created_at="2026-10-09T18:52:00+00:00",
        players=players,
        rounds=[
            Round(
                1,
                [
                    RoundEntry(1, 4, 3, -2),
                    RoundEntry(2, 1, 2, -3),
                ],
            ),
            Round(
                2,
                [
                    RoundEntry(1, 12, 0, 12),
                    RoundEntry(2, 13, 0, 13),
                ],
            ),
        ],
    )
    view = present(game)
    assert view.rounds[0].cells[0].score_label == "−2"
    assert view.rounds[0].cells[0].total_label == "−2"
    assert view.rounds[0].cells[0].parts_label == "4 Dutch, 3 Blitz"
    assert view.rounds[0].cells[1].total_label == "−3"
    assert view.rounds[1].cells[0].score_label == "+12"
    assert view.rounds[1].cells[0].total_label == "10"
    assert view.rounds[1].cells[1].total_label == "10"
    assert view.finished is True
    assert view.headline == "Ada and Bo tie with 10"


def test_a_saved_deck_is_named_on_the_player():
    players = [
        Player(1, "Ada", 1, deck_color="red", deck_icon="wheelbarrow"),
        Player(2, "Bo", 2),
    ]
    game = Game(
        id=1,
        target_score=75,
        status="active",
        created_at="2026-10-09T18:52:00+00:00",
        players=players,
        rounds=[],
    )
    view = present(game)
    assert view.players[0].deck_color == "red"
    assert view.players[0].deck_icon == "wheelbarrow"
    assert view.players[0].deck_text == "Red, Wheelbarrow"
    assert view.players[1].deck_text == ""


def test_format_timestamp_noon_midnight_and_offset():
    assert format_timestamp("2026-01-01T00:05:00+00:00") == "Jan 1, 2026, 12:05 AM UTC"
    assert format_timestamp("2026-01-01T12:00:00+00:00") == "Jan 1, 2026, 12:00 PM UTC"
    assert format_timestamp("2026-10-09T20:52:00+02:00") == "Oct 9, 2026, 6:52 PM UTC"
