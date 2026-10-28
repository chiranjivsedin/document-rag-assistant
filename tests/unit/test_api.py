import pytest
from fastapi.testclient import TestClient

from rag.api import app as api
from rag.config import get_settings

SETTINGS = get_settings({
    "DATABASE_URL": "postgresql://rag:secret@127.0.0.1:5433/rag",
    "API_CORS_ORIGINS": "http://localhost:5173",
})
OLLAMA_DOWN = "Can't reach Ollama at http://127.0.0.1:11434. Is it running? Start Ollama, or check OLLAMA_BASE_URL."


class FakeChecks:
    """Stand-in for rag.health.check_services that records its calls."""

    def __init__(self):
        self.problems = []
        self.calls = []

    def __call__(self, settings, need_chat_model):
        self.calls.append((settings, need_chat_model))
        return list(self.problems)


@pytest.fixture
def checks(monkeypatch):
    fake = FakeChecks()
    monkeypatch.setattr(api, "check_services", fake)
    return fake


@pytest.fixture
def client(checks):
    return TestClient(api.create_app(SETTINGS))


def test_health_ok(client, checks):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "llm_model": "gemma3:4b", "embedding_model": "nomic-embed-text", "problems": [],
    }
    assert checks.calls == [(SETTINGS, True)]  # both models are checked, as for ask.py


def test_health_reports_problems_with_503(client, checks):
    checks.problems = [OLLAMA_DOWN]
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["problems"] == [OLLAMA_DOWN]


def test_only_health_is_exposed_so_far(client):
    assert set(client.get("/openapi.json").json()["paths"]) == {"/health"}
    assert client.get("/docs").status_code == 200


def test_unknown_path_is_a_json_404(client):
    response = client.get("/nope")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_cors_allows_the_configured_origin_only(client):
    preflight = {"Access-Control-Request-Method": "GET"}
    allowed = client.options("/health", headers={"Origin": "http://localhost:5173", **preflight})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    other = client.options("/health", headers={"Origin": "http://evil.example", **preflight})
    assert "access-control-allow-origin" not in other.headers


def test_unexpected_errors_return_a_generic_500(checks):
    app = api.create_app(SETTINGS)

    @app.get("/boom")
    def boom():
        raise RuntimeError("could not connect with password secret")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error."}
    assert "secret" not in response.text


def test_create_app_reads_settings_when_none_given(monkeypatch):
    monkeypatch.setattr(api, "get_settings", lambda: SETTINGS)
    assert api.create_app().state.settings is SETTINGS
