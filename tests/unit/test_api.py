import threading
import time
from pathlib import Path

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
from rag.ingestion.pipeline import IngestReport
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
    assert set(client.get("/openapi.json").json()["paths"]) == {
        "/health", "/chat", "/documents/ingest", "/documents/ingest/{job_id}",
    }
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


# --- POST /documents/ingest and GET /documents/ingest/{job_id} ---------------------------------

REPORT = IngestReport(added=["acme/2.0/release-notes/notes.md"], updated=["acme/1.0/release-notes/notes.md"],
                      unchanged=["acme/1.0/deprecated-features/list.md"], removed=["acme/0.9/old.md"],
                      no_text=["acme/2.0/scan.pdf"], chunks_stored=4)
EMBEDDING_MODEL_MISSING = ollama.ResponseError('{"error": "model nomic-embed-text not found"}', 404)


class FakeIngest:
    """Stand-in for rag.service.run_ingest; can hold a job 'running' until released."""

    def __init__(self, result=REPORT, hold=False):
        self.result = result
        self.calls = []
        self.release = threading.Event()
        if not hold:
            self.release.set()

    def __call__(self, services, documents_dir):
        self.calls.append((services, documents_dir))
        self.release.wait(timeout=5)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def docs_settings(tmp_path):
    return get_settings({"DATABASE_URL": "postgresql://rag:secret@127.0.0.1:5433/rag", "DOCUMENTS_DIR": str(tmp_path)})


def ingest_client(settings, monkeypatch, fake):
    monkeypatch.setattr(api, "run_ingest", fake)
    services = Services(settings=settings, store=FakeStore([]), chat_model=None)
    return TestClient(api.create_app(settings, services=services), raise_server_exceptions=False), services


def wait_until_finished(client, job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/documents/ingest/{job_id}").json()
        if body["status"] != "running" or time.monotonic() > deadline:
            return body
        time.sleep(0.01)


def test_ingest_starts_a_job_and_reports_success(checks, docs_settings, monkeypatch):
    fake = FakeIngest()
    client, services = ingest_client(docs_settings, monkeypatch, fake)
    response = client.post("/documents/ingest")
    assert response.status_code == 202
    started = response.json()
    assert started["status"] == "running"
    assert response.headers["location"] == f"/documents/ingest/{started['job_id']}"

    body = wait_until_finished(client, started["job_id"])
    assert body["status"] == "succeeded"
    assert body["error"] is None
    assert body["finished_at"] >= body["started_at"]
    assert body["report"] == {
        "added": ["acme/2.0/release-notes/notes.md"], "updated": ["acme/1.0/release-notes/notes.md"],
        "unchanged": ["acme/1.0/deprecated-features/list.md"], "removed": ["acme/0.9/old.md"],
        "no_text": ["acme/2.0/scan.pdf"], "chunks_stored": 4,
    }
    assert fake.calls == [(services, Path(docs_settings.documents_dir))]
    assert checks.calls == [(docs_settings, False)]  # ingest needs only the embedding model, as for ingest.py


def test_polling_shows_running_until_the_job_finishes(checks, docs_settings, monkeypatch):
    fake = FakeIngest(hold=True)
    client, _ = ingest_client(docs_settings, monkeypatch, fake)
    job_id = client.post("/documents/ingest").json()["job_id"]
    running = client.get(f"/documents/ingest/{job_id}").json()
    assert running["status"] == "running"
    assert running["finished_at"] is None and running["report"] is None and running["error"] is None
    fake.release.set()
    assert wait_until_finished(client, job_id)["status"] == "succeeded"


def test_second_ingest_while_one_runs_is_409(checks, docs_settings, monkeypatch):
    fake = FakeIngest(hold=True)
    client, _ = ingest_client(docs_settings, monkeypatch, fake)
    first = client.post("/documents/ingest").json()["job_id"]
    conflict = client.post("/documents/ingest")
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "An ingestion job is already running.", "job_id": first}

    fake.release.set()
    wait_until_finished(client, first)
    again = client.post("/documents/ingest")
    assert again.status_code == 202
    assert again.json()["job_id"] != first
    assert len(fake.calls) == 2


def test_concurrent_ingest_requests_start_one_job(checks, docs_settings, monkeypatch):
    fake = FakeIngest(hold=True)
    client, _ = ingest_client(docs_settings, monkeypatch, fake)
    barrier = threading.Barrier(8)
    statuses = []

    def post():
        barrier.wait()  # all requests arrive at the same moment
        statuses.append(client.post("/documents/ingest").status_code)

    threads = [threading.Thread(target=post) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    fake.release.set()
    assert sorted(statuses) == [202] + [409] * 7
    assert len(fake.calls) == 1


UNAVAILABLE = "Ollama or the database stopped responding. Check GET /health."


@pytest.mark.parametrize("error, message", [
    (ConnectionError("Failed to connect to Ollama."), UNAVAILABLE),
    (psycopg.OperationalError("password secret rejected"), UNAVAILABLE),
    (EMBEDDING_MODEL_MISSING, "The model service failed: model nomic-embed-text not found"),
    (RuntimeError("bug near postgresql://rag:secret@db"), "Ingestion failed unexpectedly; see the server log."),
])
def test_failed_job_reports_a_safe_message(checks, docs_settings, monkeypatch, error, message):
    client, _ = ingest_client(docs_settings, monkeypatch, FakeIngest(result=error))
    job_id = client.post("/documents/ingest").json()["job_id"]
    body = wait_until_finished(client, job_id)
    assert body["status"] == "failed"
    assert body["error"] == message
    assert body["report"] is None and body["finished_at"] is not None
    assert "secret" not in client.get(f"/documents/ingest/{job_id}").text
    assert client.post("/documents/ingest").status_code == 202  # a failed job doesn't block the next one


def test_ingest_503_when_services_are_down(checks, docs_settings, monkeypatch):
    checks.problems = [OLLAMA_DOWN]
    fake = FakeIngest()
    client, _ = ingest_client(docs_settings, monkeypatch, fake)
    response = client.post("/documents/ingest")
    assert response.status_code == 503
    assert response.json() == {"detail": OLLAMA_DOWN}
    assert fake.calls == []
    checks.problems = []
    assert client.post("/documents/ingest").status_code == 202  # no job was left behind


def test_ingest_500_when_documents_folder_is_missing(checks, tmp_path, monkeypatch):
    settings = get_settings({"DOCUMENTS_DIR": str(tmp_path / "missing")})
    fake = FakeIngest()
    client, _ = ingest_client(settings, monkeypatch, fake)
    response = client.post("/documents/ingest")
    assert response.status_code == 500
    assert response.json() == {"detail": "The documents folder (DOCUMENTS_DIR) doesn't exist on the server."}
    assert fake.calls == []


def test_ingest_only_uses_the_configured_folder(checks, docs_settings, monkeypatch):
    fake = FakeIngest()
    client, _ = ingest_client(docs_settings, monkeypatch, fake)
    response = client.post("/documents/ingest?documents_dir=C:/Windows", json={"documents_dir": "C:/Windows"})
    wait_until_finished(client, response.json()["job_id"])
    assert [documents_dir for _, documents_dir in fake.calls] == [Path(docs_settings.documents_dir)]


def test_unknown_job_is_404(client):
    response = client.get("/documents/ingest/no-such-job")
    assert response.status_code == 404
    assert response.json()["detail"].startswith("Unknown ingestion job.")


def test_ingest_builds_services_lazily(checks, docs_settings, monkeypatch):
    builds = []

    def build(settings):
        builds.append(settings)
        return Services(settings=settings, store=FakeStore([]), chat_model=None)

    monkeypatch.setattr(api, "build_services", build)
    monkeypatch.setattr(api, "run_ingest", FakeIngest())
    client = TestClient(api.create_app(docs_settings))
    assert builds == []
    wait_until_finished(client, client.post("/documents/ingest").json()["job_id"])
    assert builds == [docs_settings]
