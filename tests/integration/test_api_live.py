"""Live check: the HTTP API against the local Docker pgvector database and Ollama.

Covers GET /health, ingestion through POST /documents/ingest (polled to completion) and POST /chat
answering from the document it just indexed. Uses its own temporary documents folder and table, so
your documents/ and `chunks` table are not touched.

Needs `docker compose up -d` and Ollama running with both models. Run with: pytest -m integration
"""

import os
import time

import psycopg
import pytest
from fastapi.testclient import TestClient

from rag.api.app import create_app
from rag.config import get_settings

pytestmark = pytest.mark.integration

LOCAL_DB = "postgresql://rag:rag_local_dev@127.0.0.1:5433/rag"
TABLE = "it_api_chunks"
DOCS = {
    "acme-dms/1.0/deprecated-features/deprecated.md":
        "Acme DMS 1.0 deprecated features: the Classic Desktop client is deprecated and will be removed in 2.0.",
    "acme-dms/2.0/release-notes/requirements.md":
        "Acme DMS 2.0 system requirements: the server needs at least 24 GB of RAM and 4 CPU cores.",
}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db_url = os.environ.get("DATABASE_URL", LOCAL_DB)
    documents = tmp_path_factory.mktemp("documents")
    for relative, text in DOCS.items():
        (documents / relative).parent.mkdir(parents=True, exist_ok=True)
        (documents / relative).write_text(text, encoding="utf-8")

    def drop():
        with psycopg.connect(db_url, autocommit=True) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {TABLE}")

    drop()
    settings = get_settings({"DATABASE_URL": db_url, "VECTOR_TABLE": TABLE, "DOCUMENTS_DIR": str(documents)})
    yield TestClient(create_app(settings))
    drop()


def ingest(client, timeout=300.0):
    """Start an ingestion job and poll it until it finishes."""
    response = client.post("/documents/ingest")
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/documents/ingest/{job_id}").json()
        if body["status"] != "running":
            return body
        assert time.monotonic() < deadline, f"ingestion job {job_id} still running after {timeout}s"
        time.sleep(0.5)


@pytest.fixture(scope="module")
def first_ingest(client):
    return ingest(client)


def test_health_is_ok(client):
    response = client.get("/health")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"


def test_ingest_indexes_the_documents(first_ingest):
    assert first_ingest["status"] == "succeeded", first_ingest["error"]
    assert sorted(first_ingest["report"]["added"]) == sorted(DOCS)
    assert first_ingest["report"]["chunks_stored"] == 2


def test_second_ingest_skips_unchanged_files(client, first_ingest):
    again = ingest(client)
    assert again["status"] == "succeeded", again["error"]
    assert sorted(again["report"]["unchanged"]) == sorted(DOCS)
    assert again["report"]["added"] == [] and again["report"]["chunks_stored"] == 0


def test_chat_answers_from_the_indexed_document(client, first_ingest):
    response = client.post("/chat", json={"question": "How much RAM does the server need?", "version": "2.0"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["no_answer"] is False, body["answer"]
    assert "24" in body["answer"]
    assert [source["source"] for source in body["sources"]] == ["acme-dms/2.0/release-notes/requirements.md"]
    assert body["sources"][0]["version"] == "2.0"


def test_chat_with_no_matching_documents(client, first_ingest):
    response = client.post("/chat", json={"question": "How much RAM does the server need?", "version": "9.9"})
    assert response.status_code == 200, response.text
    assert response.json()["no_answer"] is True
    assert response.json()["sources"] == []
