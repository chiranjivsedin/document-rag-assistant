import httpx
import ollama
import psycopg
import pytest
import sqlalchemy.exc
from fastapi.testclient import TestClient
from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from rag.api import app as api
from rag.config import get_settings
from rag.generation.prompts import NO_ANSWER
from rag.service import Services

SETTINGS = get_settings({
    "DATABASE_URL": "postgresql://rag:secret@127.0.0.1:5433/rag",
    "API_CORS_ORIGINS": "http://localhost:5173",
})
OLLAMA_DOWN = "Can't reach Ollama at http://127.0.0.1:11434. Is it running? Start Ollama, or check OLLAMA_BASE_URL."

PASSAGES = [
    (Document(page_content="Classic Desktop is deprecated in 1.0.",
              metadata={"source": "acme/1.0/deprecated-features/list.pdf", "product": "acme", "version": "1.0",
                        "doc_type": "deprecated-features", "page": 3, "chunk": 0}), 0.2134),
    (Document(page_content="Use Acme Web instead.",
              metadata={"source": "acme/1.0/release-notes/notes.md", "product": "acme", "version": "1.0",
                        "doc_type": "release-notes", "page": None, "chunk": 2}), 0.4),
]


class FakeChecks:
    """Stand-in for rag.health.check_services that records its calls."""

    def __init__(self):
        self.problems = []
        self.calls = []

    def __call__(self, settings, need_chat_model):
        self.calls.append((settings, need_chat_model))
        return list(self.problems)


class FakeStore:
    def __init__(self, results):
        self.results = results
        self.searches = []

    def similarity_search_with_score(self, query, k, filter):
        self.searches.append({"query": query, "k": k, "filter": filter})
        return self.results[:k]


@pytest.fixture
def checks(monkeypatch):
    fake = FakeChecks()
    monkeypatch.setattr(api, "check_services", fake)
    return fake


def make_client(results=PASSAGES, reply="Classic Desktop is deprecated [1]."):
    store = FakeStore(list(results))
    services = Services(settings=SETTINGS, store=store, chat_model=FakeListChatModel(responses=[reply]))
    return TestClient(api.create_app(SETTINGS, services=services), raise_server_exceptions=False), store


@pytest.fixture
def client(checks):
    return make_client()[0]


def failing_ask(error):
    def ask(*args, **kwargs):
        raise error
    return ask


# --- /health ---------------------------------------------------------------------------------

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


# --- app skeleton ------------------------------------------------------------------------------

def test_exposed_endpoints(client):
    assert set(client.get("/openapi.json").json()["paths"]) == {"/health", "/chat"}
    assert client.get("/docs").status_code == 200


def test_unknown_path_is_a_json_404(client):
    response = client.get("/nope")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_cors_allows_the_configured_origin_only(client):
    preflight = {"Access-Control-Request-Method": "POST"}
    allowed = client.options("/chat", headers={"Origin": "http://localhost:5173", **preflight})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    other = client.options("/chat", headers={"Origin": "http://evil.example", **preflight})
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


# --- POST /chat: success -----------------------------------------------------------------------

def test_chat_returns_answer_and_numbered_sources(checks):
    client, store = make_client()
    response = client.post("/chat", json={"question": "What is deprecated?"})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Classic Desktop is deprecated [1]."
    assert body["no_answer"] is False
    assert body["model"] == "gemma3:4b"
    assert isinstance(body["elapsed_ms"], int) and body["elapsed_ms"] >= 0
    assert body["sources"] == [
        {"number": 1, "source": "acme/1.0/deprecated-features/list.pdf", "product": "acme", "version": "1.0",
         "doc_type": "deprecated-features", "page": 3, "distance": 0.2134,
         "excerpt": "Classic Desktop is deprecated in 1.0."},
        {"number": 2, "source": "acme/1.0/release-notes/notes.md", "product": "acme", "version": "1.0",
         "doc_type": "release-notes", "page": None, "distance": 0.4, "excerpt": "Use Acme Web instead."},
    ]
    assert store.searches == [{"query": "What is deprecated?", "k": 5, "filter": None}]
    assert checks.calls == [(SETTINGS, True)]


def test_chat_passes_filters_and_k(checks):
    client, store = make_client()
    response = client.post("/chat", json={"question": "  What is deprecated?  ", "product": " acme ",
                                          "version": "1.0", "k": 1})
    assert response.status_code == 200
    assert store.searches == [{"query": "What is deprecated?", "k": 1, "filter": {"version": "1.0", "product": "acme"}}]
    assert len(response.json()["sources"]) == 1


def test_chat_empty_filters_mean_no_filter(checks):
    client, store = make_client()
    client.post("/chat", json={"question": "Q?", "product": "", "version": "  "})
    assert store.searches[0]["filter"] is None


def test_chat_excerpt_is_shortened(checks):
    long_passage = [(Document(page_content="x" * 1000, metadata={"source": "a.md"}), 0.1)]
    client, _ = make_client(results=long_passage)
    source = client.post("/chat", json={"question": "Q?"}).json()["sources"][0]
    assert source["excerpt"] == "x" * 300
    assert source["product"] is None and source["page"] is None


def test_chat_with_nothing_retrieved(checks):
    client, _ = make_client(results=[], reply="the model should not be called")
    body = client.post("/chat", json={"question": "Anything?", "version": "9.9"}).json()
    assert body["answer"] == NO_ANSWER
    assert body["no_answer"] is True
    assert body["sources"] == []


def test_chat_when_the_model_does_not_know(checks):
    client, _ = make_client(reply=NO_ANSWER)
    body = client.post("/chat", json={"question": "Unrelated?"}).json()
    assert body["answer"] == NO_ANSWER
    assert body["no_answer"] is True
    assert body["sources"] == []


# --- POST /chat: validation (422) --------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {},
    {"question": ""},
    {"question": "   "},
    {"question": "x" * 2001},
    {"question": 42},
    {"question": "Q?", "k": 0},
    {"question": "Q?", "k": 21},
    {"question": "Q?", "k": "five"},
    {"question": "Q?", "version": "v" * 101},
    {"question": "Q?", "versoin": "1.0"},
])
def test_chat_rejects_invalid_input(client, checks, payload):
    response = client.post("/chat", json=payload)
    assert response.status_code == 422
    assert checks.calls == []  # rejected before any service is touched


def test_chat_accepts_the_limits(checks):
    client, _ = make_client()
    assert client.post("/chat", json={"question": "x" * 2000, "k": 20}).status_code == 200
    assert client.post("/chat", json={"question": "Q?", "k": 1}).status_code == 200


# --- POST /chat: errors ------------------------------------------------------------------------

def test_chat_503_when_services_are_down(client, checks, monkeypatch):
    checks.problems = [OLLAMA_DOWN]
    monkeypatch.setattr(api, "ask", failing_ask(AssertionError("ask should not run")))
    response = client.post("/chat", json={"question": "Q?"})
    assert response.status_code == 503
    assert response.json() == {"detail": OLLAMA_DOWN}


@pytest.mark.parametrize("error", [
    ConnectionError("Failed to connect to Ollama."),
    ConnectionRefusedError("Connect call failed ('127.0.0.1', 5433)"),
    psycopg.OperationalError("connection to 127.0.0.1 failed: password secret"),
    sqlalchemy.exc.OperationalError("SELECT 1", {}, Exception("server closed the connection")),
])
def test_chat_503_when_a_backend_drops_mid_request(client, checks, monkeypatch, error):
    monkeypatch.setattr(api, "ask", failing_ask(error))
    response = client.post("/chat", json={"question": "Q?"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Ollama or the database stopped responding. Check GET /health."}


def test_chat_502_passes_on_ollamas_reason(client, checks, monkeypatch):
    error = ollama.ResponseError('{"error":"model requires more system memory (4.0 GiB) than is available (3.4 GiB)"}', 500)
    monkeypatch.setattr(api, "ask", failing_ask(error))
    response = client.post("/chat", json={"question": "Q?"})
    assert response.status_code == 502
    assert response.json() == {
        "detail": "The model service failed: model requires more system memory (4.0 GiB) than is available (3.4 GiB)"
    }


def test_chat_502_on_a_broken_model_connection(client, checks, monkeypatch):
    monkeypatch.setattr(api, "ask", failing_ask(httpx.ReadTimeout("timed out reading from 127.0.0.1:11434")))
    response = client.post("/chat", json={"question": "Q?"})
    assert response.status_code == 502
    assert response.json() == {"detail": "The model service failed: ReadTimeout"}


def test_chat_500_hides_unexpected_errors(client, checks, monkeypatch):
    monkeypatch.setattr(api, "ask", failing_ask(RuntimeError("bug near postgresql://rag:secret@db")))
    response = client.post("/chat", json={"question": "Q?"})
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error."}
    assert "secret" not in response.text


# --- POST /chat: services built lazily ---------------------------------------------------------

def test_services_are_built_once_on_first_use(checks, monkeypatch):
    builds = []

    def build(settings):
        builds.append(settings)
        return Services(settings=settings, store=FakeStore(PASSAGES), chat_model=FakeListChatModel(responses=["A [1]."]))

    monkeypatch.setattr(api, "build_services", build)
    client = TestClient(api.create_app(SETTINGS))
    assert builds == []  # nothing is built at startup
    client.get("/health")
    assert builds == []
    assert client.post("/chat", json={"question": "Q?"}).status_code == 200
    assert client.post("/chat", json={"question": "Q?"}).status_code == 200
    assert builds == [SETTINGS]


def test_a_failed_build_is_retried_on_the_next_request(checks, monkeypatch):
    attempts = []

    def build(settings):
        attempts.append(1)
        if len(attempts) == 1:
            raise psycopg.OperationalError("connection timeout expired")
        return Services(settings=settings, store=FakeStore(PASSAGES), chat_model=FakeListChatModel(responses=["A [1]."]))

    monkeypatch.setattr(api, "build_services", build)
    client = TestClient(api.create_app(SETTINGS), raise_server_exceptions=False)
    assert client.post("/chat", json={"question": "Q?"}).status_code == 503
    assert client.post("/chat", json={"question": "Q?"}).status_code == 200
    assert len(attempts) == 2
