"""The pages used at the table: create a game, score it, fix it, look back."""

import re

from fastapi.testclient import TestClient

from app.main import create_app
from tests.fakes import FakeVerifier


def start_game(client: TestClient, names: list[str], target: str = "75") -> str:
    response = client.post(
        "/games",
        data={"target_score": target, "player_name": names},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return response.headers["location"]


def player_ids(html: str) -> list[str]:
    return re.findall(r'name="dutch_(\d+)"', html)


def test_home_explains_the_rules_and_default_target(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "2 to 8 players" in response.text
    assert 'value="75"' in response.text
    assert "Finished games will be listed here." in response.text
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_one_player_and_nine_players_are_rejected(client):
    solo = client.post(
        "/games",
        data={"target_score": "75", "player_name": ["Ada", ""]},
        follow_redirects=False,
    )
    assert solo.status_code == 400
    assert "2 to 8" in solo.text
    assert 'value="Ada"' in solo.text
    assert "Start game" in solo.text
    assert 'href="/games/' not in solo.text

    rejected = client.post(
        "/games",
        data={"target_score": "75", "player_name": [f"P{index}" for index in range(1, 10)]},
        follow_redirects=False,
    )
    assert rejected.status_code == 400
    assert "2 to 8" in rejected.text
    assert 'value="P1"' in rejected.text
    assert 'value="P8"' in rejected.text


def test_eight_players_get_eight_score_rows(client):
    names = [f"P{index}" for index in range(1, 9)]
    location = start_game(client, names, "75")
    page = client.get(location)
    assert page.status_code == 200
    assert len(player_ids(page.text)) == 8
    assert "Round 1" in page.text


def test_score_a_negative_round_then_a_tie_then_undo(client):
    location = start_game(client, ["Ada", "Bo"], "10")
    page = client.get(location)
    assert "First to 10" in page.text
    ada_id, bo_id = player_ids(page.text)

    saved = client.post(
        f"{location}/rounds",
        data={
            f"dutch_{ada_id}": "4",
            f"blitz_{ada_id}": "3",
            f"dutch_{bo_id}": "1",
            f"blitz_{bo_id}": "2",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    page = client.get(location)
    assert "Ada leads with −2" in page.text
    assert "−3" in page.text
    assert "4 Dutch, 3 Blitz" in page.text
    assert "Save round 2" in page.text
    assert "Fix last round" in page.text

    client.post(
        f"{location}/rounds",
        data={
            f"dutch_{ada_id}": "12",
            f"blitz_{ada_id}": "0",
            f"dutch_{bo_id}": "13",
            f"blitz_{bo_id}": "0",
        },
        follow_redirects=False,
    )
    page = client.get(location)
    assert "Ada and Bo tie with 10" in page.text
    assert "No tiebreaker" in page.text
    assert "Save round" not in page.text
    assert "Undo last round and resume" in page.text

    home = client.get("/")
    assert "Ada and Bo tie with 10" in home.text
    assert "Past games" in home.text

    undone = client.post(f"{location}/undo", follow_redirects=False)
    assert undone.status_code == 303
    page = client.get(location)
    assert "Ada leads with −2" in page.text
    assert "Ada and Bo tie" not in page.text
    assert "Save round 2" in page.text

    home = client.get("/")
    assert "Current games" in home.text
    assert "Ada leads with −2" in home.text


def test_invalid_blitz_count_keeps_what_was_typed(client):
    location = start_game(client, ["Ada", "Bo"], "75")
    page = client.get(location)
    ada_id, bo_id = player_ids(page.text)
    rejected = client.post(
        f"{location}/rounds",
        data={
            f"dutch_{ada_id}": "4",
            f"blitz_{ada_id}": "11",
            f"dutch_{bo_id}": "1",
            f"blitz_{bo_id}": "0",
        },
        follow_redirects=False,
    )
    assert rejected.status_code == 400
    assert "0 to 10" in rejected.text
    assert 'value="11"' in rejected.text
    assert "Round 1" in client.get(location).text


def test_edit_last_round_reopens_the_game(client):
    location = start_game(client, ["Ada", "Bo"], "10")
    page = client.get(location)
    ada_id, bo_id = player_ids(page.text)
    client.post(
        f"{location}/rounds",
        data={
            f"dutch_{ada_id}": "12",
            f"blitz_{ada_id}": "0",
            f"dutch_{bo_id}": "0",
            f"blitz_{bo_id}": "0",
        },
        follow_redirects=False,
    )
    edit = client.get(f"{location}?edit=1")
    assert "Update round 1" in edit.text
    assert 'value="12"' in edit.text

    updated = client.post(
        f"{location}/rounds/last",
        data={
            f"dutch_{ada_id}": "3",
            f"blitz_{ada_id}": "0",
            f"dutch_{bo_id}": "0",
            f"blitz_{bo_id}": "0",
        },
        follow_redirects=False,
    )
    assert updated.status_code == 303
    page = client.get(location)
    assert "Ada leads with 3" in page.text
    assert "Save round 2" in page.text


def test_missing_game_page(client):
    response = client.get("/games/999")
    assert response.status_code == 404
    assert "Game not found" in response.text


def test_player_add_page_offers_colors_and_their_icons(client):
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="player-add"' in page.text
    assert "Deck color" in page.text
    assert "Deck icon" in page.text
    assert "Wheelbarrow" in page.text
    assert 'xmlns="http://www.w3.org/2000/svg"' in page.text
    assert 'name="player_color_1" value="red" data-default-icon="carriage" checked' in page.text
    assert 'name="player_icon_1" value="carriage" checked' in page.text
    assert 'name="player_color_2" value="blue" data-default-icon="plow" checked' in page.text
    assert "cdn." not in page.text


def test_deck_color_and_icon_show_on_the_scoreboard(client):
    created = client.post(
        "/games",
        data={
            "target_score": "75",
            "player_name": ["Ada", "Bo"],
            "player_color_1": "red",
            "player_icon_1": "wheelbarrow",
            "player_color_2": "navy",
            "player_icon_2": "wheelbarrow",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303, created.text
    location = created.headers["location"]
    page = client.get(location)
    assert "Red, Wheelbarrow" in page.text
    assert "Navy blue, Wheelbarrow" in page.text
    assert page.text.count('data-color="red"') >= 2
    assert page.text.count('data-icon="wheelbarrow"') >= 3
    assert 'class="chip deck-red' in page.text
    assert "<svg" in page.text

    ada_id, bo_id = player_ids(page.text)
    saved = client.post(
        f"{location}/rounds",
        data={
            f"dutch_{ada_id}": "4",
            f"blitz_{ada_id}": "0",
            f"dutch_{bo_id}": "1",
            f"blitz_{bo_id}": "0",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    page = client.get(location)
    assert "Score sheet" in page.text
    assert page.text.count('data-icon="wheelbarrow"') >= 4
    assert "Ada leads with 4" in page.text

    home = client.get("/")
    assert "Ada" in home.text
    assert 'data-icon="wheelbarrow"' in home.text
    assert "Red, Wheelbarrow" in home.text


def test_same_deck_color_is_rejected_on_the_form(client):
    rejected = client.post(
        "/games",
        data={
            "target_score": "75",
            "player_name": ["Ada", "Bo"],
            "player_color_1": "green",
            "player_icon_1": "pump",
            "player_color_2": "green",
            "player_icon_2": "carriage",
        },
        follow_redirects=False,
    )
    assert rejected.status_code == 400
    assert "Green is already chosen" in rejected.text
    assert 'value="Ada"' in rejected.text
    assert 'name="player_color_1" value="green" data-default-icon="pump" checked' in rejected.text
    assert 'name="player_icon_2" value="carriage" checked' in rejected.text
    assert "Current games" not in client.get("/").text


def test_a_color_without_an_icon_uses_the_official_icon(client):
    created = client.post(
        "/games",
        data={
            "target_score": "75",
            "player_name": ["Ada", "Bo"],
            "player_color_1": "yellow",
            "player_color_2": "orange",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303, created.text
    page = client.get(created.headers["location"])
    assert "Yellow, Pail" in page.text
    assert "Orange, Hat" in page.text
    assert 'data-icon="pail"' in page.text
    assert 'data-icon="hat"' in page.text


def test_database_file_is_created_at_the_configured_path(tmp_path):
    path = tmp_path / "nested" / "blitz.sqlite"
    application = create_app(str(path), verifier=FakeVerifier())
    with TestClient(application) as local_client:
        local_client.cookies.set("__session", "user:user_test")
        assert local_client.get("/").status_code == 200
    assert path.is_file()
