import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.fakes import FakeVerifier


@pytest.fixture(autouse=True)
def _activity_stamp_stays_next_to_the_test_database(monkeypatch):
    # config.py loads a gitignored .env on import. A DUTCH_BLITZ_ACTIVITY_PATH
    # in that file must not make the suite write outside the test tmp dir.
    monkeypatch.delenv("DUTCH_BLITZ_ACTIVITY_PATH", raising=False)


@pytest.fixture
def client(tmp_path):
    application = create_app(str(tmp_path / "test.sqlite"), verifier=FakeVerifier())
    with TestClient(application) as test_client:
        test_client.cookies.set("__session", "user:user_test")
        yield test_client
