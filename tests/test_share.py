"""Invite links, seat claims, and who may change a shared game."""

import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.errors import ForbiddenError, NotFoundError, ScoringError
from app.main import create_app
from app.store import GameStore
from tests.fakes import FakeVerifier

OWNER = "user_owner"
BO = "user_bo"
CY = "user_cy"
OUTSIDER = "user_out"


@pytest.fixture
def client(tmp_path):
    application = create_app(str(tmp_path / "share.sqlite"), verifier=FakeVerifier())
    with TestClient(application) as test_client:
        yield test_client


def auth(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer user:{user}"}


def start(client: TestClient, user: str = OWNER) -> str:
    response = client.post(
        "/games",
        data={"target_score": "10", "player_name": ["Ada", "Bo"]},
        headers=auth(user),
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return response.headers["location"]


def player_ids(html: str) -> list[str]:
    return re.findall(r'name="dutch_(\d+)"', html)


def invite_token(html: str) -> str:
    match = re.search(r"/join/([A-Za-z0-9_-]{20,})", html)
    assert match is not None
    return match.group(1)


def test_owner_invite_join_score_and_outsider_is_hidden(client):
    location = start(client)
    page = client.get(location, headers=auth(OWNER))
    assert "Create invite link" in page.text
    assert "/join/" not in page.text
    ada_id, bo_id = player_ids(page.text)

    created = client.post(
        f"{location}/invite",
        data={"mode": "create"},
        headers=auth(OWNER),
        follow_redirects=False,
    )
    assert created.status_code == 303
    again = client.post(
        f"{location}/invite",
        data={"mode": "create"},
        headers=auth(OWNER),
        follow_redirects=False,
    )
    assert again.status_code == 303
    owner_page = client.get(location, headers=auth(OWNER))
    token = invite_token(owner_page.text)
    assert owner_page.text.count(token) >= 1

    hidden = client.get(location, headers=auth(OUTSIDER))
    assert hidden.status_code == 404
    assert "Ada" not in hidden.text
    assert token not in hidden.text

    invite = client.get(f"/join/{token}", headers=auth(BO))
    assert invite.status_code == 200
    assert "Play as Bo" in invite.text
    assert "Play as Ada" in invite.text
    joined = client.post(
        f"/join/{token}",
        data={"player_id": bo_id},
        headers=auth(BO),
        follow_redirects=False,
    )
    assert joined.status_code == 303
    assert joined.headers["location"] == location

    second_seat = client.post(
        f"/join/{token}",
        data={"player_id": ada_id},
        headers=auth(BO),
        follow_redirects=False,
    )
    assert second_seat.status_code == 400
    assert "already have a seat" in second_seat.text

    taken = client.post(
        f"/join/{token}",
        data={"player_id": bo_id},
        headers=auth(CY),
        follow_redirects=False,
    )
    assert taken.status_code == 400
    assert "already taken" in taken.text

    home = client.get("/", headers=auth(BO))
    assert "Ada, Bo" in home.text
    assert "Shared ·" in home.text
    assert "Shared ·" not in client.get("/", headers=auth(OWNER)).text

    playing = client.get(location, headers=auth(BO))
    assert "You're playing as Bo" in playing.text
    assert "Save round 1" in playing.text
    assert token not in playing.text
    assert "Create invite link" not in playing.text
    assert "Undo last round" not in playing.text
    assert "Fix last round" not in playing.text

    saved = client.post(
        f"{location}/rounds",
        data={
            f"dutch_{ada_id}": "4",
            f"blitz_{ada_id}": "0",
            f"dutch_{bo_id}": "1",
            f"blitz_{bo_id}": "0",
        },
        headers=auth(BO),
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert "Ada leads with 4" in client.get(location, headers=auth(OWNER)).text

    for path, payload in (
        (f"{location}/undo", {}),
        (
            f"{location}/rounds/last",
            {
                f"dutch_{ada_id}": "1",
                f"blitz_{ada_id}": "0",
                f"dutch_{bo_id}": "1",
                f"blitz_{bo_id}": "0",
            },
        ),
        (f"{location}/invite", {"mode": "rotate"}),
        (f"{location}/seats/{bo_id}/revoke", {}),
    ):
        denied = client.post(path, data=payload, headers=auth(BO))
        assert denied.status_code == 403
        assert "Only the person who started this game" in denied.text
        assert token not in denied.text
    assert "Ada leads with 4" in client.get(location, headers=auth(OWNER)).text

    blocked = client.post(
        f"{location}/rounds",
        data={f"dutch_{ada_id}": "1", f"blitz_{ada_id}": "0"},
        headers=auth(OUTSIDER),
    )
    assert blocked.status_code == 404
    assert "Ada" not in blocked.text


def test_revoke_rotate_and_disable_leave_history_intact(client):
    location = start(client)
    page = client.get(location, headers=auth(OWNER))
    ada_id, bo_id = player_ids(page.text)
    client.post(f"{location}/invite", data={"mode": "create"}, headers=auth(OWNER), follow_redirects=False)
    token = invite_token(client.get(location, headers=auth(OWNER)).text)
    client.post(f"/join/{token}", data={"player_id": bo_id}, headers=auth(BO), follow_redirects=False)
    client.post(
        f"{location}/rounds",
        data={
            f"dutch_{ada_id}": "2",
            f"blitz_{ada_id}": "0",
            f"dutch_{bo_id}": "2",
            f"blitz_{bo_id}": "0",
        },
        headers=auth(BO),
        follow_redirects=False,
    )

    revoked = client.post(
        f"{location}/seats/{bo_id}/revoke",
        headers=auth(OWNER),
        follow_redirects=False,
    )
    assert revoked.status_code == 303
    assert client.get(location, headers=auth(BO)).status_code == 404
    assert "Ada" not in client.get("/", headers=auth(BO)).text
    assert "All tied at 2" in client.get(location, headers=auth(OWNER)).text

    client.post(f"/join/{token}", data={"player_id": bo_id}, headers=auth(BO), follow_redirects=False)
    rotated = client.post(
        f"{location}/invite",
        data={"mode": "rotate"},
        headers=auth(OWNER),
        follow_redirects=False,
    )
    assert rotated.status_code == 303
    fresh = invite_token(client.get(location, headers=auth(OWNER)).text)
    assert fresh != token
    assert client.get(f"/join/{token}", headers=auth(CY)).status_code == 404
    assert "isn't active" in client.get(f"/join/{token}", headers=auth(CY)).text
    assert "Ada" not in client.get(f"/join/{token}", headers=auth(CY)).text
    assert client.get(location, headers=auth(BO)).status_code == 200

    joined = client.post(
        f"/join/{fresh}",
        data={"player_id": ada_id},
        headers=auth(CY),
        follow_redirects=False,
    )
    assert joined.status_code == 303
    client.post(
        f"{location}/invite",
        data={"mode": "disable"},
        headers=auth(OWNER),
        follow_redirects=False,
    )
    assert "Create invite link" in client.get(location, headers=auth(OWNER)).text
    assert fresh not in client.get(location, headers=auth(OWNER)).text
    assert client.get(f"/join/{fresh}", headers=auth(OUTSIDER)).status_code == 404
    assert client.get(location, headers=auth(CY)).status_code == 200
    assert client.get(location, headers=auth(BO)).status_code == 200


def test_owner_can_claim_a_seat_without_an_invite(client):
    location = start(client)
    page = client.get(location, headers=auth(OWNER))
    ada_id, _bo_id = player_ids(page.text)
    claimed = client.post(
        f"{location}/seats/{ada_id}/claim",
        headers=auth(OWNER),
        follow_redirects=False,
    )
    assert claimed.status_code == 303
    page = client.get(location, headers=auth(OWNER))
    assert "You're playing as Ada" in page.text
    assert ">You</span>" in page.text
    assert "Create invite link" in page.text
    assert "Undo last round" not in page.text


def test_signed_out_join_does_not_claim_a_seat(client, tmp_path):
    location = start(client)
    client.post(f"{location}/invite", data={"mode": "create"}, headers=auth(OWNER), follow_redirects=False)
    token = invite_token(client.get(location, headers=auth(OWNER)).text)
    denied = client.get(f"/join/{token}")
    assert denied.status_code == 401
    assert "Ada" not in denied.text
    with sqlite3.connect(tmp_path / "share.sqlite") as conn:
        claimed = conn.execute("SELECT COUNT(*) FROM players WHERE user_id IS NOT NULL").fetchone()[0]
    assert claimed == 0


def test_store_migration_keeps_owned_games_and_hides_unowned_ones(tmp_path):
    legacy = tmp_path / "legacy.sqlite"
    with sqlite3.connect(legacy) as conn:
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
    store = GameStore(str(legacy))
    store.init()
    assert store.get_game(1, OWNER) is None
    assert store.list_games(OWNER) == []
    with pytest.raises(NotFoundError):
        store.set_invite(1, owner_id=OWNER, mode="create")
    with sqlite3.connect(legacy) as conn:
        assert conn.execute("SELECT owner_id, invite_token FROM games").fetchone() == (None, None)
        assert conn.execute("SELECT user_id FROM players").fetchall() == [(None,), (None,)]

    current = tmp_path / "owned.sqlite"
    with sqlite3.connect(current) as conn:
        conn.executescript(
            """
            CREATE TABLE games (
                id INTEGER PRIMARY KEY,
                target_score INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                owner_id TEXT
            );
            CREATE TABLE players (
                id INTEGER PRIMARY KEY,
                game_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                seat INTEGER NOT NULL
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
            VALUES (75, '2024-01-01T00:00:00+00:00', 'active', 'user_owner');
            INSERT INTO players (game_id, name, seat) VALUES (1, 'Ada', 1), (1, 'Bo', 2);
            """
        )
    owned = GameStore(str(current))
    owned.init()
    game = owned.get_game(1, OWNER)
    assert game is not None
    assert game.invite_token is None
    assert [player.user_id for player in game.players] == [None, None]
    assert owned.list_games(OUTSIDER) == []
    token = owned.set_invite(1, owner_id=OWNER, mode="create")
    assert token
    joined = owned.join_game(token, game.players[1].id, user_id=BO)
    assert joined == 1
    assert owned.list_games(BO)[0].id == 1
    with pytest.raises(ForbiddenError):
        owned.undo_last_round(1, owner_id=BO)
    with pytest.raises(ScoringError, match="no round"):
        owned.undo_last_round(1, owner_id=OWNER)
