"""Live check: document statuses kept in step with ingestion jobs, in PostgreSQL.

The documents table, the status queries, chunk counts and adoption all run against the real database, in
their own tables. The ingestion run itself is a stand-in that writes chunk rows with SQL, so Ollama isn't
needed and runs can be held, failed or overlapped on purpose. Needs `docker compose up -d`.
Run with: pytest -m integration
"""

import io
import os
import threading
import time

import psycopg
import pytest
from fastapi.testclient import TestClient

from rag import database
from rag.api import app as api_app
from rag.config import get_settings
from rag.dms.index import index_access
from rag.dms.library import DocumentLibrary, build_library
from rag.dms.repository import DocumentRepository
from rag.dms.storage import DocumentStorage
from rag.dms.tracking import NO_TEXT
from rag.ingestion.loaders import file_metadata, iter_files
from rag.ingestion.pipeline import IngestReport, index_fingerprint
from rag.service import Services

pytestmark = pytest.mark.integration

LOCAL_DB = "postgresql://rag:rag_local_dev@127.0.0.1:5433/rag"
DOCUMENTS_TABLE = "it_track_documents"
CHUNKS_TABLE = "it_track_chunks"
FINGERPRINT = index_fingerprint(get_settings({}))  # what the real pipeline stores as embedding_model
FORM = {"product": "acme", "version": "1.0", "doc_type": "notes"}


class SqlPipeline:
    """Stands in for the ingestion run: brings the chunks table in line with the folder like the real
    pipeline (two chunk rows per file; files with "scan" in the name have no text)."""

    def __init__(self, db_url):
        self.db_url = db_url
        self.calls = self.running = self.max_running = 0
        self.gate: threading.Event | None = None
        self.error: BaseException | None = None
        self._lock = threading.Lock()

    def __call__(self, services, documents_dir) -> IngestReport:
        with self._lock:
            self.calls += 1
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            if self.gate is not None:
                assert self.gate.wait(10)
            if self.error is not None:
                raise self.error
            return self._sync(documents_dir)
        finally:
            with self._lock:
                self.running -= 1

    def _sync(self, documents_dir) -> IngestReport:
        indexed = database.indexed_files(self.db_url, CHUNKS_TABLE)
        report, on_disk = IngestReport(), set()
        with psycopg.connect(self.db_url) as conn:
            for path in iter_files(documents_dir):
                meta = file_metadata(path, documents_dir)
                source = meta["source"]
                on_disk.add(source)
                previous = indexed.get(source)
                if previous == (meta["file_hash"], FINGERPRINT):
                    report.unchanged.append(source)
                    continue
                conn.execute(f"DELETE FROM {CHUNKS_TABLE} WHERE source = %s", (source,))
                if "scan" in path.name:
                    report.no_text.append(source)
                    continue
                for _ in range(2):
                    conn.execute(f"INSERT INTO {CHUNKS_TABLE} (source, file_hash, embedding_model) VALUES (%s, %s, %s)",
                                 (source, meta["file_hash"], FINGERPRINT))
                (report.updated if previous else report.added).append(source)
            for source in sorted(set(indexed) - on_disk):
                conn.execute(f"DELETE FROM {CHUNKS_TABLE} WHERE source = %s", (source,))
                report.removed.append(source)
        return report


@pytest.fixture
def db_url():
    url = os.environ.get("DATABASE_URL", LOCAL_DB)

    def drop():
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {DOCUMENTS_TABLE}")
            conn.execute(f"DROP TABLE IF EXISTS {CHUNKS_TABLE}")

    drop()
    with psycopg.connect(url) as conn:
        conn.execute(f"CREATE TABLE {CHUNKS_TABLE} (id serial PRIMARY KEY, source text NOT NULL, "
                     "file_hash text, embedding_model text)")
    yield url
    drop()


@pytest.fixture
def setup(db_url, tmp_path, monkeypatch):
    """A real library (documents table + chunks table in PostgreSQL) behind the API, with the SQL pipeline."""
    settings = get_settings({"DATABASE_URL": db_url, "DOCUMENTS_DIR": str(tmp_path), "VECTOR_TABLE": CHUNKS_TABLE})
    repository = DocumentRepository(db_url, table=DOCUMENTS_TABLE)
    repository.create_table()
    library = DocumentLibrary(DocumentStorage(tmp_path, max_bytes=1024 * 1024), repository, index_access(settings))
    pipeline = SqlPipeline(db_url)
    monkeypatch.setattr(api_app, "run_ingest", pipeline)
    monkeypatch.setattr(api_app, "check_services", lambda settings, need_chat_model: [])
    services = Services(settings=settings, store=None, chat_model=None)
    client = TestClient(api_app.create_app(settings, services=services, library=library),
                        raise_server_exceptions=False)
    return client, library, pipeline


def upload(client, name, text="text"):
    return client.post("/documents", data=FORM, files={"file": (name, text.encode(), "text/markdown")}).json()


def documents(client):
    return {d["source"]: d for d in client.get("/documents").json()["documents"]}


def wait_for(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def settled(client):
    return all(d["status"] in ("indexed", "failed") for d in documents(client).values())


def test_adopting_existing_files_uses_the_real_index(setup, db_url, tmp_path):
    _, library, _ = setup
    indexed = tmp_path / "acme" / "1.0" / "notes" / "indexed.md"
    indexed.parent.mkdir(parents=True)
    indexed.write_text("already indexed", encoding="utf-8")
    (tmp_path / "acme" / "1.0" / "notes" / "new.md").write_text("never indexed", encoding="utf-8")
    meta = file_metadata(indexed, tmp_path)
    with psycopg.connect(db_url) as conn:
        for _ in range(3):
            conn.execute(f"INSERT INTO {CHUNKS_TABLE} (source, file_hash, embedding_model) VALUES (%s, %s, %s)",
                         (meta["source"], meta["file_hash"], FINGERPRINT))

    assert library.tracker.adopt_untracked() == 2

    rows = {d.source: d for d in library.repository.list_all()}
    assert (rows["acme/1.0/notes/indexed.md"].status, rows["acme/1.0/notes/indexed.md"].chunk_count) == ("indexed", 3)
    assert rows["acme/1.0/notes/new.md"].status == "pending"
    assert indexed.read_text(encoding="utf-8") == "already indexed"  # used where it is


def test_upload_is_indexed_automatically_with_its_real_chunk_count(setup):
    client, _, pipeline = setup
    body = upload(client, "notes.md")
    assert body["ingest_job_id"]

    wait_for(lambda: settled(client))
    document = documents(client)[body["source"]]
    assert (document["status"], document["chunk_count"], document["error"]) == ("indexed", 2, None)
    assert pipeline.calls == 1


def test_uploads_during_a_run_and_a_scanned_file(setup):
    client, _, pipeline = setup
    pipeline.gate = threading.Event()
    first = upload(client, "first.md")
    wait_for(lambda: pipeline.running == 1)
    late = upload(client, "late.md", "late")
    scan = upload(client, "scan.md", "no text")
    assert late["ingest_job_id"] is None and scan["ingest_job_id"] is None

    pipeline.gate.set()
    wait_for(lambda: settled(client))

    rows = documents(client)
    assert rows[first["source"]]["status"] == "indexed"
    assert rows[late["source"]]["status"] == "indexed"
    assert (rows[scan["source"]]["status"], rows[scan["source"]]["error"]) == ("failed", NO_TEXT)
    assert pipeline.max_running == 1 and pipeline.calls == 2


def test_a_failed_run_only_fails_what_it_claimed_and_the_next_run_retries(setup):
    client, _, pipeline = setup
    indexed = upload(client, "indexed.md")
    wait_for(lambda: settled(client))

    pipeline.error = psycopg.OperationalError("connection lost")
    broken = upload(client, "broken.md", "other")
    wait_for(lambda: settled(client))
    rows = documents(client)
    assert rows[indexed["source"]]["status"] == "indexed"  # indexed earlier: untouched
    assert (rows[broken["source"]]["status"], rows[broken["source"]]["error"]) == (
        "failed", "Ollama or the database stopped responding. Check GET /health.")

    pipeline.error = None
    job = client.post("/documents/ingest").json()
    wait_for(lambda: client.get(f"/documents/ingest/{job['job_id']}").json()["status"] != "running")
    assert documents(client)[broken["source"]]["status"] == "indexed"


def test_concurrent_uploads_run_one_ingestion_at_a_time(setup):
    client, _, pipeline = setup
    pipeline.gate = threading.Event()
    bodies = []
    threads = [threading.Thread(target=lambda n=n: bodies.append(upload(client, f"doc-{n}.md", f"document {n}")))
               for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(bodies) == 8 and sum(b["ingest_job_id"] is not None for b in bodies) == 1

    pipeline.gate.set()
    wait_for(lambda: settled(client))
    assert {d["status"] for d in documents(client).values()} == {"indexed"}
    assert pipeline.max_running == 1


def test_documents_left_processing_by_a_restart_are_released(setup, db_url):
    client, library, _ = setup
    body = upload(client, "notes.md")
    wait_for(lambda: settled(client))
    with psycopg.connect(db_url) as conn:  # as if the server stopped mid-run
        conn.execute(f"UPDATE {DOCUMENTS_TABLE} SET status = 'processing'")

    job = client.post("/documents/ingest").json()
    wait_for(lambda: client.get(f"/documents/ingest/{job['job_id']}").json()["status"] != "running")
    assert documents(client)[body["source"]]["status"] == "indexed"


def test_build_library_at_startup_releases_and_adopts(db_url, tmp_path):
    settings = get_settings({"DATABASE_URL": db_url, "DOCUMENTS_DIR": str(tmp_path), "VECTOR_TABLE": CHUNKS_TABLE})
    first = build_library(settings, table=DOCUMENTS_TABLE)
    stuck = first.upload(io.BytesIO(b"text"), "stuck.md", "acme", "1.0", "notes")
    first.repository.claim_for_indexing()  # the previous server stopped mid-run
    by_hand = tmp_path / "acme" / "1.0" / "notes" / "by-hand.md"
    by_hand.write_text("put here while the server was down", encoding="utf-8")

    restarted = build_library(settings, table=DOCUMENTS_TABLE)

    rows = {d.source: d for d in restarted.repository.list_all()}
    assert rows[stuck.source].status == "pending"
    assert (rows["acme/1.0/notes/by-hand.md"].origin, rows["acme/1.0/notes/by-hand.md"].status) == ("folder", "pending")
