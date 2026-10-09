"""Environment settings used to run the app."""

import pytest

from app.config import db_path, host, port


def test_defaults(monkeypatch):
    monkeypatch.delenv("DUTCH_BLITZ_DB_PATH", raising=False)
    monkeypatch.delenv("DUTCH_BLITZ_HOST", raising=False)
    monkeypatch.delenv("DUTCH_BLITZ_PORT", raising=False)
    assert db_path() == "data/dutch_blitz.sqlite"
    assert host() == "127.0.0.1"
    assert port() == 8000


def test_overrides(monkeypatch):
    monkeypatch.setenv("DUTCH_BLITZ_DB_PATH", "scores.sqlite")
    monkeypatch.setenv("DUTCH_BLITZ_HOST", "0.0.0.0")
    monkeypatch.setenv("DUTCH_BLITZ_PORT", "9000")
    assert db_path() == "scores.sqlite"
    assert host() == "0.0.0.0"
    assert port() == 9000


@pytest.mark.parametrize("raw", ["1", "65535", "8000"])
def test_good_ports(monkeypatch, raw):
    monkeypatch.setenv("DUTCH_BLITZ_PORT", raw)
    assert port() == int(raw)


@pytest.mark.parametrize("raw", ["0", "65536", "nope", "", "-1"])
def test_bad_ports(monkeypatch, raw):
    monkeypatch.setenv("DUTCH_BLITZ_PORT", raw)
    with pytest.raises(SystemExit):
        port()
