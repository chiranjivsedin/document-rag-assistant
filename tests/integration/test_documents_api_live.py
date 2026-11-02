"""Live check: the /documents endpoints with the real documents table and chunk deletion in PostgreSQL.

Uses its own tables and a temporary documents folder; Ollama is not needed. Needs `docker compose up -d`.
Run with: pytest -m integration
"""

import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from rag import database
from rag.api import app as api_app
from rag.config import get_settings
from rag.dms.index import index_access
from rag.dms.library import DocumentLibrary
from rag.dms.repository import DocumentRepository
from rag.dms.storage import DocumentStorage

pytestmark = pytest.mark.integration

LOCAL_DB = "postgresql://rag:rag_local_dev@127.0.0.1:5433/rag"
DOCUMENTS_TABLE = "it_dms_documents"
CHUNKS_TABLE = "it_dms_chunks"
FORM = {"product": "documentum", "version": "16.2", "doc_type": "release-notes"}


@pytest.fixture
def db_url():
    url = os.environ.get("DATABASE_URL", LOCAL_DB)

    def drop():
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {DOCUMENTS_TABLE}")
            conn.execute(f"DROP TABLE IF EXISTS {CHUNKS_TABLE}")

    drop()
    yield url
    drop()


@pytest.fixture
def client(db_url, tmp_path, monkeypatch):
    # These tests cover storage and the endpoints; uploads mustn't start real indexing here (that is
    # test_documents_tracking_live's job), so the services are reported down.
    monkeypatch.setattr(api_app, "check_services", lambda settings, need_chat_model: ["not in this test"])
    settings = get_settings({"DATABASE_URL": db_url, "DOCUMENTS_DIR": str(tmp_path), "VECTOR_TABLE": CHUNKS_TABLE})
    repository = DocumentRepository(db_url, table=DOCUMENTS_TABLE)
    repository.create_table()
    library = DocumentLibrary(DocumentStorage(tmp_path, max_bytes=1024 * 1024), repository, index_access(settings))
    return TestClient(api_app.create_app(settings, library=library), raise_server_exceptions=False)


def chunk_sources(db_url):
    with psycopg.connect(db_url) as conn:
        return sorted(row[0] for row in conn.execute(f"SELECT source FROM {CHUNKS_TABLE}"))


def test_delete_chunks_without_a_chunks_table(db_url):
    assert database.delete_chunks(db_url, CHUNKS_TABLE, "anything.md") == 0


def test_upload_list_open_and_delete(client, db_url, tmp_path):
    uploaded = client.post("/documents", data=FORM, files={"file": ("Notes – v2.md", "# Webtop – déprécié €".encode(),
                                                                     "text/markdown")})
    assert uploaded.status_code == 201
    document = uploaded.json()
    assert document["status"] == "pending" and document["filename"] == "Notes – v2.md"

    other = client.post("/documents", data=FORM, files={"file": ("other.txt", b"Other", "text/plain")}).json()
    assert client.post("/documents", data=FORM,
                       files={"file": ("other.txt", b"Again", "text/plain")}).status_code == 409

    listing = client.get("/documents").json()["documents"]
    assert [d["id"] for d in listing] == [other["id"], document["id"]]
    assert document.pop("ingest_job_id") is None  # services reported down in this test: not indexed
    assert client.get(f"/documents/{document['id']}").json() == document

    opened = client.get(f"/documents/{document['id']}/file")
    assert opened.status_code == 200
    assert opened.content.decode() == "# Webtop – déprécié €"
    assert opened.headers["content-type"] == "text/plain; charset=utf-8"

    # Stand in for indexed passages of both files, then delete one document.
    with psycopg.connect(db_url) as conn:
        conn.execute(f"CREATE TABLE {CHUNKS_TABLE} (id serial PRIMARY KEY, source text NOT NULL)")
        conn.execute(f"INSERT INTO {CHUNKS_TABLE} (source) VALUES (%s), (%s), (%s)",
                     (document["source"], document["source"], other["source"]))

    assert client.delete(f"/documents/{document['id']}").status_code == 204
    assert chunk_sources(db_url) == [other["source"]]  # only that document's passages
    assert not (tmp_path / document["source"]).exists()
    assert (tmp_path / other["source"]).exists()
    assert [d["id"] for d in client.get("/documents").json()["documents"]] == [other["id"]]
