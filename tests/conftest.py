import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.fakes import FakeVerifier


@pytest.fixture
def client(tmp_path):
    application = create_app(str(tmp_path / "test.sqlite"), verifier=FakeVerifier())
    with TestClient(application) as test_client:
        test_client.cookies.set("__session", "user:user_test")
        yield test_client
