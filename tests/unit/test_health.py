import io
import json
from contextlib import contextmanager
from urllib.error import URLError

import psycopg
import pytest

from rag import health
from rag.config import get_settings

SETTINGS = get_settings({"DATABASE_URL": "postgresql://rag:secret@127.0.0.1:5433/rag"})


def ollama_with(*names):
    """Stand-in for urlopen returning Ollama's /api/tags listing these models."""
    @contextmanager
    def fake_urlopen(url, timeout):
        yield io.BytesIO(json.dumps({"models": [{"name": n} for n in names]}).encode())
    return fake_urlopen


@contextmanager
def db_ok(url):
    yield object()


def db_down(url):
    raise psycopg.OperationalError("connection timeout expired")


@pytest.fixture(autouse=True)
def healthy(monkeypatch):
    monkeypatch.setattr(health, "urlopen", ollama_with("gemma3:4b", "nomic-embed-text:latest"))
    monkeypatch.setattr(health.connection, "connect", db_ok)


def test_all_good():
    assert health.check_services(SETTINGS, need_chat_model=True) == []


def test_ollama_not_running(monkeypatch):
    def refuse(url, timeout):
        raise URLError("connection refused")
    monkeypatch.setattr(health, "urlopen", refuse)
    assert health.check_services(SETTINGS, need_chat_model=True) == [
        "Can't reach Ollama at http://127.0.0.1:11434. Is it running? Start Ollama, or check OLLAMA_BASE_URL."
    ]


def test_missing_chat_model_is_named_with_the_fix(monkeypatch):
    monkeypatch.setattr(health, "urlopen", ollama_with("nomic-embed-text:latest"))
    assert health.check_services(SETTINGS, need_chat_model=True) == [
        "Ollama model 'gemma3:4b' is not installed. Run: ollama pull gemma3:4b"
    ]


def test_chat_model_not_needed_for_ingest(monkeypatch):
    monkeypatch.setattr(health, "urlopen", ollama_with("nomic-embed-text:latest"))
    assert health.check_services(SETTINGS, need_chat_model=False) == []


def test_database_down_message_hides_password(monkeypatch):
    monkeypatch.setattr(health.connection, "connect", db_down)
    [message] = health.check_services(SETTINGS, need_chat_model=False)
    assert message.startswith("Can't connect to the database at 127.0.0.1:5433 (connection timeout expired).")
    assert "docker compose up -d" in message
    assert "secret" not in message


def test_wrong_password_gets_its_own_hint(monkeypatch):
    def bad_password(url):
        raise psycopg.OperationalError('FATAL:  password authentication failed for user "rag"')
    monkeypatch.setattr(health.connection, "connect", bad_password)
    [message] = health.check_services(SETTINGS, need_chat_model=False)
    assert message.endswith("Check the user name and password in DATABASE_URL.")
    assert "docker compose" not in message


def test_missing_database_url():
    [message] = health.check_services(get_settings({}), need_chat_model=False)
    assert message.startswith("DATABASE_URL is not set")
