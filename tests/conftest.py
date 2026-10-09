import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    application = create_app(str(tmp_path / "test.sqlite"))
    with TestClient(application) as test_client:
        yield test_client
