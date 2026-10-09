"""The pages used at the table: create a game, score it, fix it, look back."""

import re

from fastapi.testclient import TestClient

from app.main import create_app


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


def test_database_file_is_created_at_the_configured_path(tmp_path):
    path = tmp_path / "nested" / "blitz.sqlite"
    application = create_app(str(path))
    with TestClient(application) as local_client:
        assert local_client.get("/").status_code == 200
    assert path.is_file()
