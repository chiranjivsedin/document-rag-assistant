import asyncio
import gc
import io
import json
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from rag.api import app as api
from rag.api.jobs import IngestJob, IngestJobs
from rag.config import get_settings
from rag.dms.library import DocumentLibrary
from rag.dms.storage import DocumentStorage
from rag.service import Services

FORM = {"product": "documentum", "version": "16.2", "doc_type": "release-notes"}


def pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def settings_for(documents_dir, **env):
    return get_settings({"DATABASE_URL": "postgresql://rag:secret@127.0.0.1:5433/rag",
                         "DOCUMENTS_DIR": str(documents_dir), "MAX_UPLOAD_MB": "1", **env})


class FakeChecks:
    """Stands in for rag.health.check_services. Services are reported down unless a test brings them up, so
    an upload only starts indexing in the tests about that."""

    def __init__(self):
        self.problems = ["Can't reach Ollama (test)."]

    def __call__(self, settings, need_chat_model):
        return list(self.problems)


@pytest.fixture(autouse=True)
def checks(monkeypatch):
    fake = FakeChecks()
    monkeypatch.setattr(api, "check_services", fake)
    return fake


@pytest.fixture
def library(tmp_path, fake_repository, fake_index):
    return DocumentLibrary(DocumentStorage(tmp_path, max_bytes=1024 * 1024), fake_repository, fake_index.access())


@pytest.fixture
def client(tmp_path, library):
    return TestClient(api.create_app(settings_for(tmp_path), library=library), raise_server_exceptions=False)


def post(client, data=b"# Webtop is deprecated", filename="notes.md", content_type="text/markdown", **form):
    return client.post("/documents", data={**FORM, **form}, files={"file": (filename, data, content_type)})


# Upload

def test_upload_returns_the_pending_document(client, tmp_path):
    response = post(client, filename="Release Notes.md")

    assert response.status_code == 201
    body = response.json()
    assert response.headers["location"] == f"/documents/{body['id']}"
    assert body["status"] == "pending" and body["origin"] == "upload" and body["error"] is None
    assert body["source"] == "documentum/16.2/release-notes/Release Notes.md"
    assert body["filename"] == "Release Notes.md" and body["size_bytes"] == 22
    assert (tmp_path / "documentum" / "16.2" / "release-notes" / "Release Notes.md").is_file()


def test_upload_a_pdf(client):
    response = post(client, data=pdf_bytes(), filename="notes.pdf", content_type="application/pdf")
    assert response.status_code == 201
    assert response.json()["content_type"] == "application/pdf"


@pytest.mark.parametrize("changes, message", [
    ({"filename": "notes.exe"}, "Only .md, .pdf, .txt files can be uploaded."),
    ({"version": "../.."}, "version must be"),
    ({"data": b"%PDF-1.4 broken", "filename": "notes.pdf"}, "This PDF can't be read"),
    ({"data": b"\xff\xfe binary", "filename": "notes.txt"}, "UTF-8"),
])
def test_invalid_uploads_are_422_with_a_reason(client, tmp_path, changes, message):
    response = post(client, **changes)
    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_missing_form_fields_are_422(client):
    response = client.post("/documents", data={"product": "documentum"},
                           files={"file": ("notes.md", b"text", "text/markdown")})
    assert response.status_code == 422


def test_duplicate_path_is_409(client):
    assert post(client).status_code == 201
    response = post(client, data=b"different content")
    assert response.status_code == 409
    assert response.json() == {"detail": "A document already exists at documentum/16.2/release-notes/notes.md."}


def test_oversized_upload_is_refused_from_its_content_length(client, tmp_path):
    response = post(client, data=b"x" * (2 * 1024 * 1024), filename="big.txt")
    assert response.status_code == 413
    assert response.json() == {"detail": "The file is larger than the 1 MB limit."}
    assert not (tmp_path / ".uploads").exists()  # refused before anything was read or written


def test_upload_just_over_the_limit_is_refused_while_saving(client, tmp_path):
    response = post(client, data=b"x" * (1024 * 1024 + 10), filename="big.txt")
    assert response.status_code == 413
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def multipart_body(data: bytes, filename="notes.md", boundary="test-boundary") -> tuple[bytes, str]:
    """A multipart upload body built by hand, so it can be streamed without a Content-Length."""
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
             for name, value in FORM.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 f"Content-Type: text/plain\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def chunked(body: bytes, size=64 * 1024):
    for start in range(0, len(body), size):
        yield body[start:start + size]


def test_chunked_upload_without_content_length_is_accepted(client, tmp_path):
    body, content_type = multipart_body(b"# Sent in chunks")
    response = client.post("/documents", content=chunked(body, size=7), headers={"Content-Type": content_type})

    assert "content-length" not in response.request.headers
    assert response.request.headers["transfer-encoding"] == "chunked"
    assert response.status_code == 201
    assert (tmp_path / "documentum" / "16.2" / "release-notes" / "notes.md").read_bytes() == b"# Sent in chunks"


def test_oversized_chunked_upload_is_413_and_leaves_nothing(client, tmp_path):
    body, content_type = multipart_body(b"x" * (2 * 1024 * 1024), filename="big.txt")
    response = client.post("/documents", content=chunked(body), headers={"Content-Type": content_type})

    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert response.json() == {"detail": "The file is larger than the 1 MB limit."}
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def call_upload(app, chunks: list[bytes], headers: list[tuple[bytes, bytes]]) -> tuple[int, dict, int]:
    """Send POST /documents straight to the ASGI app, one body chunk per receive(); report how many it read."""
    consumed = 0
    sent = []

    async def receive():
        nonlocal consumed
        if consumed < len(chunks):
            consumed += 1
            return {"type": "http.request", "body": chunks[consumed - 1], "more_body": consumed < len(chunks)}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": "/documents", "raw_path": b"/documents", "query_string": b"", "root_path": "",
             "headers": [(b"host", b"testserver"), *headers], "client": ("127.0.0.1", 1),
             "server": ("testserver", 80)}
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(body), consumed


def test_reading_stops_as_soon_as_a_chunked_upload_passes_the_limit(tmp_path, library):
    app = api.create_app(settings_for(tmp_path), library=library)  # 1 MB limit
    head, content_type = multipart_body(b"")
    head = head[:head.rindex(b"\r\n--")]  # everything up to the file's data
    chunks = [head] + [b"x" * (64 * 1024)] * 160  # 10 MB that a client keeps sending

    status, body, consumed = call_upload(app, chunks, [(b"content-type", content_type.encode()),
                                                       (b"transfer-encoding", b"chunked")])

    assert status == 413 and body == {"detail": "The file is larger than the 1 MB limit."}
    assert consumed <= 19  # 1 MB + 64 KB of form overhead, in 64 KB chunks: the other ~9 MB are never read
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_invalid_content_length_is_400(tmp_path, library):
    app = api.create_app(settings_for(tmp_path), library=library)
    body, content_type = multipart_body(b"text")
    status, response, consumed = call_upload(app, [body], [(b"content-type", content_type.encode()),
                                                           (b"content-length", b"12abc")])
    assert status == 400 and response == {"detail": "Invalid Content-Length header."}
    assert consumed == 0


def test_database_outage_during_upload_is_503_and_removes_the_file(client, fake_repository, tmp_path):
    fake_repository.fail_on["add"] = psycopg.OperationalError("password secret rejected")
    response = post(client)
    assert response.status_code == 503
    assert "secret" not in response.text
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_unexpected_insert_error_is_a_generic_500_and_removes_the_file(client, fake_repository, tmp_path):
    fake_repository.fail_on["add"] = RuntimeError("bug with secret details")
    response = post(client)
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error."}
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


# Listing and reading

def test_list_and_get(client):
    first = post(client, filename="a.md").json()
    second = post(client, filename="b.md").json()

    listing = client.get("/documents")
    assert listing.status_code == 200
    assert [d["id"] for d in listing.json()["documents"]] == [second["id"], first["id"]]
    assert client.get(f"/documents/{first['id']}").json() == {k: v for k, v in first.items() if k != "ingest_job_id"}


def test_unknown_and_malformed_ids(client):
    assert client.get(f"/documents/{uuid.uuid4()}").status_code == 404
    assert client.get("/documents/not-a-uuid").status_code == 422
    assert client.get(f"/documents/{uuid.uuid4()}/file").json() == {"detail": "Unknown document."}


def test_list_during_a_database_outage_is_503(client, fake_repository):
    fake_repository.fail_on["list_all"] = psycopg.OperationalError("down")
    response = client.get("/documents")
    assert response.status_code == 503
    assert response.json() == {"detail": "Ollama or the database stopped responding. Check GET /health."}


def test_existing_ingest_routes_still_win(client):
    assert client.get("/documents/ingest/abc").status_code == 404
    assert client.get("/documents/ingest/abc").json()["detail"].startswith("Unknown ingestion job")


# The original file

def test_pdf_opens_inline(client):
    data = pdf_bytes()
    document = post(client, data=data, filename="Notes (final).pdf", content_type="application/pdf").json()

    response = client.get(f"/documents/{document['id']}/file")
    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["content-disposition"] == "inline; filename*=utf-8''Notes%20%28final%29.pdf"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_download_is_an_attachment(client):
    document = post(client).json()
    response = client.get(f"/documents/{document['id']}/file", params={"download": "true"})
    assert response.headers["content-disposition"].startswith("attachment;")


def test_markdown_and_html_looking_text_are_served_as_plain_text(client):
    document = post(client, data=b"<script>alert(1)</script>", filename="page.md").json()
    response = client.get(f"/documents/{document['id']}/file")
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_non_ascii_names_download_safely(client):
    document = post(client, filename="Résumé.md").json()
    response = client.get(f"/documents/{document['id']}/file", params={"download": "true"})
    assert "filename*=utf-8''R%C3%A9sum%C3%A9.md" in response.headers["content-disposition"]


def test_missing_original_file_is_404(client, tmp_path):
    document = post(client).json()
    (tmp_path / document["source"]).unlink()
    response = client.get(f"/documents/{document['id']}/file")
    assert response.status_code == 404
    assert response.json() == {"detail": "The original file is missing on the server."}


# Deleting

def test_delete_removes_everything(client, fake_index, tmp_path):
    document = post(client).json()
    response = client.delete(f"/documents/{document['id']}")
    assert response.status_code == 204
    assert not (tmp_path / document["source"]).exists()
    assert fake_index.deleted == [document["source"]]
    assert client.get(f"/documents/{document['id']}").status_code == 404
    assert client.delete(f"/documents/{document['id']}").status_code == 404


def test_delete_is_refused_while_ingestion_runs(client, tmp_path, monkeypatch):
    document = post(client).json()
    job = IngestJob(id="job-1", status="running", started_at=datetime.now(timezone.utc))
    monkeypatch.setattr(IngestJobs, "running", lambda self: job)

    response = client.delete(f"/documents/{document['id']}")
    assert response.status_code == 409
    assert response.json()["job_id"] == "job-1"
    assert (tmp_path / document["source"]).exists()


def test_delete_with_a_locked_file_reports_nothing_deleted(client, library, monkeypatch, tmp_path):
    document = post(client).json()
    monkeypatch.setattr(library.storage, "delete", lambda source: (_ for _ in ()).throw(PermissionError("in use")))

    response = client.delete(f"/documents/{document['id']}")
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Nothing was deleted: the original file couldn't be removed (it may be open in another program). "
                  "Try again later.",
        "file_deleted": False, "chunks_deleted": False, "record_deleted": False,
    }
    assert (tmp_path / document["source"]).exists()


def test_delete_stopped_by_a_database_outage_says_what_was_done_and_can_be_retried(client, fake_index, tmp_path):
    document = post(client).json()
    fake_index.error = psycopg.OperationalError("down")

    response = client.delete(f"/documents/{document['id']}")
    assert response.status_code == 503
    body = response.json()
    assert (body["file_deleted"], body["chunks_deleted"], body["record_deleted"]) == (True, False, False)
    assert "Delete the document again to finish" in body["detail"]
    assert client.get(f"/documents/{document['id']}").status_code == 200  # still listed

    fake_index.error = None
    assert client.delete(f"/documents/{document['id']}").status_code == 204


def test_delete_with_a_record_failure(client, fake_repository):
    document = post(client).json()
    fake_repository.fail_on["delete"] = RuntimeError("unexpected")
    response = client.delete(f"/documents/{document['id']}")
    assert response.status_code == 500
    body = response.json()
    assert (body["file_deleted"], body["chunks_deleted"], body["record_deleted"]) == (True, True, False)
    assert "Delete it again to finish" in body["detail"]


# Configuration problems

def test_without_a_database_url_the_library_is_503(tmp_path):
    settings = get_settings({"DOCUMENTS_DIR": str(tmp_path)})
    client = TestClient(api.create_app(settings), raise_server_exceptions=False)
    response = client.get("/documents")
    assert response.status_code == 503
    assert response.json() == {"detail": "DATABASE_URL is not set, so documents can't be stored."}


def test_missing_documents_folder_is_500(tmp_path, library):
    client = TestClient(api.create_app(settings_for(tmp_path / "missing"), library=library),
                        raise_server_exceptions=False)
    response = post(client)
    assert response.status_code == 500
    assert "DOCUMENTS_DIR" in response.json()["detail"]


def test_library_is_built_on_first_use_and_a_failure_is_retried(tmp_path, monkeypatch, library):
    calls = []

    def build(settings):
        calls.append(settings)
        if len(calls) == 1:
            raise psycopg.OperationalError("database starting")
        return library

    monkeypatch.setattr(api, "build_library", build)
    client = TestClient(api.create_app(settings_for(tmp_path)), raise_server_exceptions=False)
    assert client.get("/health").status_code in (200, 503)
    assert calls == []  # not built at startup
    assert client.get("/documents").status_code == 503
    assert client.get("/documents").status_code == 200
    assert len(calls) == 2


def test_cors_preflight_allows_delete(client):
    response = client.options(f"/documents/{uuid.uuid4()}", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "DELETE"})
    assert response.status_code == 200
    assert "DELETE" in response.headers["access-control-allow-methods"]


# Indexing after upload (the existing ingestion job runner, with document statuses kept in step)

@pytest.fixture
def indexing(tmp_path, library, fake_pipeline, checks, monkeypatch):
    """A client whose uploads start indexing: services up, and the ingestion run is the fake pipeline."""
    checks.problems = []
    monkeypatch.setattr(api, "run_ingest", fake_pipeline)
    settings = settings_for(tmp_path)
    services = Services(settings=settings, store=None, chat_model=None)
    return TestClient(api.create_app(settings, services=services, library=library), raise_server_exceptions=False)


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def status(client, document_id):
    return client.get(f"/documents/{document_id}").json()["status"]


def test_upload_starts_indexing_and_the_document_becomes_indexed(indexing, fake_pipeline):
    body = post(indexing).json()
    assert body["ingest_job_id"]

    wait_for(lambda: indexing.get(f"/documents/ingest/{body['ingest_job_id']}").json()["status"] != "running")
    job = indexing.get(f"/documents/ingest/{body['ingest_job_id']}").json()
    assert job["status"] == "succeeded" and job["report"]["added"] == [body["source"]]
    document = indexing.get(f"/documents/{body['id']}").json()
    assert (document["status"], document["chunk_count"], document["error"]) == ("indexed", 2, None)
    assert fake_pipeline.calls == 1


def test_upload_while_services_are_down_stays_pending(client, fake_pipeline):
    body = post(client).json()  # the default client reports Ollama down
    assert body["ingest_job_id"] is None and body["status"] == "pending"
    assert fake_pipeline.calls == 0
    assert status(client, body["id"]) == "pending"


def test_upload_during_a_running_job_is_indexed_by_a_follow_up_job(indexing, fake_pipeline):
    fake_pipeline.gate = threading.Event()
    first = post(indexing, filename="first.md").json()
    wait_for(lambda: fake_pipeline.running == 1)

    second = post(indexing, filename="second.md").json()
    assert second["ingest_job_id"] is None  # a job is running: no second job, and no error
    assert status(indexing, second["id"]) == "pending"

    fake_pipeline.gate.set()
    wait_for(lambda: status(indexing, second["id"]) == "indexed")
    assert status(indexing, first["id"]) == "indexed"
    assert fake_pipeline.calls == 2 and fake_pipeline.max_running == 1


def test_a_failed_job_marks_its_documents_failed_with_the_reason(indexing, fake_pipeline):
    fake_pipeline.error = psycopg.OperationalError("password secret rejected")
    body = post(indexing).json()

    wait_for(lambda: status(indexing, body["id"]) != "pending" and status(indexing, body["id"]) != "processing")
    document = indexing.get(f"/documents/{body['id']}").json()
    assert document["status"] == "failed"
    assert document["error"] == "Ollama or the database stopped responding. Check GET /health."
    assert "secret" not in indexing.get("/documents").text
    assert indexing.get(f"/documents/ingest/{body['ingest_job_id']}").json()["status"] == "failed"


def test_the_ingest_endpoint_updates_statuses_and_adopts_files(indexing, checks, tmp_path, fake_pipeline):
    checks.problems = ["Can't reach Ollama (test)."]  # uploads don't start a job: the endpoint does
    waiting = post(indexing).json()
    by_hand = tmp_path / "acme" / "2.0" / "notes" / "by-hand.md"
    by_hand.parent.mkdir(parents=True)
    by_hand.write_text("Put here by hand", encoding="utf-8")
    checks.problems = []

    response = indexing.post("/documents/ingest")
    assert response.status_code == 202
    assert set(response.json()) == {"job_id", "status", "started_at", "finished_at", "report", "error"}  # unchanged
    wait_for(lambda: indexing.get(f"/documents/ingest/{response.json()['job_id']}").json()["status"] != "running")

    documents = {d["source"]: d for d in indexing.get("/documents").json()["documents"]}
    assert documents[waiting["source"]]["status"] == "indexed"
    adopted = documents["acme/2.0/notes/by-hand.md"]
    assert (adopted["origin"], adopted["status"], adopted["chunk_count"]) == ("folder", "indexed", 2)


def test_concurrent_uploads_never_run_two_ingestions_at_once(indexing, fake_pipeline):
    fake_pipeline.gate = threading.Event()
    bodies, errors = [], []

    def upload(number):
        try:
            bodies.append(post(indexing, filename=f"doc-{number}.md", data=f"document {number}".encode()).json())
        except Exception as error:  # pragma: no cover - reported below
            errors.append(error)

    threads = [threading.Thread(target=upload, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and len(bodies) == 6
    assert sum(b["ingest_job_id"] is not None for b in bodies) == 1  # exactly one upload started a job

    fake_pipeline.gate.set()
    wait_for(lambda: all(status(indexing, b["id"]) == "indexed" for b in bodies))
    assert fake_pipeline.max_running == 1
    assert fake_pipeline.calls <= 2  # the first job, plus at most one follow-up for uploads made meanwhile


# Unsafe paths become a clear 500 (R5)

UNSAFE = ("The document's file can't be used safely on this server (its path or a link on the way isn't allowed); "
          "see the server log.")


def junction(link, target):
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, OSError):
        pytest.skip("can't create a junction here (not Windows)")


def test_upload_into_a_linked_folder_is_a_clear_500(client, tmp_path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    junction(tmp_path / "documentum", outside)
    response = post(client)
    assert response.status_code == 500 and response.json() == {"detail": UNSAFE}
    assert list(outside.rglob("*")) == []


def test_a_hard_linked_original_is_not_served(client, tmp_path, tmp_path_factory):
    document = post(client).json()
    outside = tmp_path_factory.mktemp("outside")
    stored = tmp_path / document["source"]
    stored.unlink()
    (outside / "secret.md").write_text("secret")
    os.link(outside / "secret.md", stored)  # same name, now another name for a file outside

    response = client.get(f"/documents/{document['id']}/file")
    assert response.status_code == 500 and response.json() == {"detail": UNSAFE}
    assert "secret" not in response.text


def test_deleting_through_a_link_deletes_nothing(client, tmp_path, tmp_path_factory, fake_index):
    document = post(client).json()
    outside = tmp_path_factory.mktemp("outside")
    folder = tmp_path / "documentum" / "16.2" / "release-notes"
    (outside / "notes.md").write_bytes((folder / "notes.md").read_bytes())
    (folder / "notes.md").unlink()
    folder.rmdir()
    junction(folder, outside)  # the document's folder now leads outside

    response = client.delete(f"/documents/{document['id']}")
    assert response.status_code == 500
    assert response.json() == {"detail": f"Nothing was deleted. {UNSAFE}",
                               "file_deleted": False, "chunks_deleted": False, "record_deleted": False}
    assert (outside / "notes.md").exists() and fake_index.deleted == []


# Chunked uploads leave no temporary files behind

@pytest.fixture
def spool_dir(tmp_path_factory, monkeypatch):
    """Where the form parser spools uploads over 1 MB (Python's temp folder), redirected to an empty folder."""
    folder = tmp_path_factory.mktemp("spool")
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


@pytest.mark.parametrize("size, expected", [
    (5 * 1024 * 1024, 413),        # past the body cap: reading stops
    (1024 * 1024 + 10, 413),       # body under the cap, file just over the limit: refused when saving
    (900 * 1024, 201),
])
def test_chunked_uploads_leave_no_temporary_files(client, tmp_path, spool_dir, size, expected):
    body, content_type = multipart_body(b"x" * size, filename="big.txt")
    response = client.post("/documents", content=chunked(body), headers={"Content-Type": content_type})

    assert response.status_code == expected
    gc.collect()
    assert list(spool_dir.iterdir()) == []  # the parser's spooled copy is gone
    assert not (tmp_path / ".uploads").exists() or list((tmp_path / ".uploads").iterdir()) == []
    stored = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert len(stored) == (1 if expected == 201 else 0)


# Startup recovery never touches a running job

def test_requests_during_a_running_job_never_release_its_claims(tmp_path, library, fake_repository, fake_pipeline,
                                                                checks, monkeypatch):
    checks.problems = []
    monkeypatch.setattr(api, "run_ingest", fake_pipeline)
    builds, releases = [], []
    monkeypatch.setattr(api, "build_library", lambda settings: builds.append(1) or library)
    real_release = fake_repository.release_processing
    monkeypatch.setattr(fake_repository, "release_processing", lambda: releases.append(1) or real_release())
    settings = settings_for(tmp_path)
    client = TestClient(api.create_app(settings, services=Services(settings=settings, store=None, chat_model=None)),
                        raise_server_exceptions=False)
    fake_pipeline.gate = threading.Event()

    first = post(client, filename="first.md").json()
    wait_for(lambda: fake_pipeline.running == 1)
    assert status(client, first["id"]) == "processing"
    client.get("/documents")
    post(client, filename="second.md")
    assert client.post("/documents/ingest").status_code == 409

    assert status(client, first["id"]) == "processing"  # still claimed by the running job
    assert len(builds) == 1 and len(releases) == 1  # one build; one release, at the start of that job's run

    fake_pipeline.gate.set()
    wait_for(lambda: {d["status"] for d in client.get("/documents").json()["documents"]} == {"indexed"})


# Master data: uploads use the lists, documents carry tags (rag.dms.tags)

TAGGED_FORM = {"product": "content-management", "version": "24.4", "doc_type": "release-notes"}


@pytest.fixture
def tagged(tmp_path, fake_repository, fake_index, fake_tags):
    """A client whose library has the master lists, with an extra single-value category "module" and a multi-value
    "audience"."""
    fake_tags.add_category("module", "Module", multiple=False)
    fake_tags.add_category("audience", "Audience", multiple=True)
    for key, code, label in [("module", "workflow", "Workflow"), ("module", "search", "Search"),
                             ("audience", "admins", "Administrators"), ("audience", "developers", "Developers")]:
        fake_tags.add_value(key, code, label)
    library = DocumentLibrary(DocumentStorage(tmp_path, max_bytes=1024 * 1024), fake_repository,
                              fake_index.access(), tags=fake_tags)
    return TestClient(api.create_app(settings_for(tmp_path), library=library), raise_server_exceptions=False)


def post_tagged(client, filename="notes.md", tags=(), **form):
    return client.post("/documents", data={**TAGGED_FORM, **form, "tag": [str(t) for t in tags]},
                       files={"file": (filename, b"# Notes", "text/markdown")})


@pytest.mark.parametrize("field, value, message", [
    ("version", "9.9", "Version '9.9' is not in the master list."),
    ("product", "acme", "Product 'acme' is not in the master list."),
    ("doc_type", "Release-Notes", "Document type 'Release-Notes' is not in the master list."),
])
def test_upload_refuses_a_code_not_in_the_list(tagged, tmp_path, field, value, message):
    response = post_tagged(tagged, **{field: value})
    assert response.status_code == 422
    assert response.json() == {"detail": message}
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_upload_refuses_a_retired_code(tagged, fake_tags, tmp_path):
    fake_tags.update_value(fake_tags.value("version", "24.4").id, active=False)
    response = post_tagged(tagged)
    assert response.status_code == 422
    assert response.json() == {"detail": "Version '24.4' is retired; choose another."}
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_upload_with_extra_tags(tagged, fake_tags):
    workflow, admins, developers = (fake_tags.value("module", "workflow"), fake_tags.value("audience", "admins"),
                                    fake_tags.value("audience", "developers"))
    response = post_tagged(tagged, tags=[workflow.id, admins.id, developers.id])
    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "content-management/24.4/release-notes/notes.md"
    assert [(t["category"], t["code"], t["label"], t["active"]) for t in body["tags"]] == [
        ("module", "workflow", "Workflow", True), ("audience", "admins", "Administrators", True),
        ("audience", "developers", "Developers", True)]
    assert body["unlisted"] == []
    assert tagged.get(f"/documents/{body['id']}").json()["tags"] == body["tags"]


@pytest.mark.parametrize("pick, message", [
    (lambda t: [t.value("module", "workflow").id, t.value("module", "search").id], "Module takes one value only."),
    (lambda t: [t.value("version", "24.2").id], "Version comes from"),
    (lambda t: [99999], "Tag value 99999 is not in the master lists."),
])
def test_upload_refuses_invalid_extra_tags_and_leaves_nothing(tagged, fake_tags, fake_repository, tmp_path,
                                                               pick, message):
    response = post_tagged(tagged, tags=pick(fake_tags))
    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert fake_repository.rows == {}
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_a_failed_tagging_undoes_the_upload(tagged, fake_tags, fake_repository, tmp_path):
    fake_tags.fail_on["set_document_tags"] = psycopg.OperationalError("down")
    response = post_tagged(tagged, tags=[fake_tags.value("module", "workflow").id])
    assert response.status_code == 503
    assert fake_repository.rows == {}
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_put_tags_replaces_extra_tags(tagged, fake_tags):
    document = post_tagged(tagged, tags=[fake_tags.value("module", "workflow").id]).json()
    search, developers = fake_tags.value("module", "search"), fake_tags.value("audience", "developers")

    response = tagged.put(f"/documents/{document['id']}/tags", json={"value_ids": [search.id, developers.id]})
    assert response.status_code == 200
    assert [t["code"] for t in response.json()["tags"]] == ["search", "developers"]
    assert [t["code"] for t in tagged.get(f"/documents/{document['id']}").json()["tags"]] == ["search", "developers"]

    response = tagged.put(f"/documents/{document['id']}/tags", json={"value_ids": []})
    assert response.status_code == 200 and response.json()["tags"] == []


def test_put_tags_needs_no_admin_key(tagged, fake_tags):
    document = post_tagged(tagged).json()
    response = tagged.put(f"/documents/{document['id']}/tags",
                          json={"value_ids": [fake_tags.value("module", "search").id]})
    assert response.status_code == 200


def test_put_tags_refuses_builtin_or_retired(tagged, fake_tags):
    document = post_tagged(tagged).json()
    path = f"/documents/{document['id']}/tags"
    response = tagged.put(path, json={"value_ids": [fake_tags.value("version", "24.4").id]})
    assert response.status_code == 422 and "comes from the document's folder" in response.json()["detail"]

    retired = fake_tags.update_value(fake_tags.value("audience", "admins").id, active=False)
    response = tagged.put(path, json={"value_ids": [retired.id]})
    assert response.status_code == 422 and "retired" in response.json()["detail"]

    assert tagged.put(path, json={"value_ids": [99999]}).status_code == 422
    assert tagged.put(path, json={"value_ids": ["x"]}).status_code == 422
    assert tagged.put(path, json={"value_ids": [], "product": "x"}).status_code == 422
    assert tagged.put(f"/documents/{uuid.uuid4()}/tags", json={"value_ids": []}).status_code == 404
    assert tagged.get(path.removesuffix("/tags")).json()["tags"] == []


def test_list_includes_tags_and_unlisted(tagged, fake_tags):
    tagged_doc = post_tagged(tagged, tags=[fake_tags.value("module", "workflow").id]).json()
    retired_doc = post_tagged(tagged, filename="b.md", version="24.2").json()
    fake_tags.update_value(fake_tags.value("version", "24.2").id, active=False)

    documents = {d["id"]: d for d in tagged.get("/documents").json()["documents"]}
    assert [t["code"] for t in documents[tagged_doc["id"]]["tags"]] == ["workflow"]
    assert documents[tagged_doc["id"]]["unlisted"] == []
    assert documents[retired_doc["id"]]["unlisted"] == []  # retired is still in the list
    assert documents[retired_doc["id"]]["tags"] == []


def test_an_adopted_file_with_values_not_in_the_lists_is_marked(tmp_path, fake_repository, fake_index, fake_tags):
    put_by_hand = tmp_path / "acme" / "1.0" / "guide" / "old.md"
    put_by_hand.parent.mkdir(parents=True)
    put_by_hand.write_text("old", encoding="utf-8")
    library = DocumentLibrary(DocumentStorage(tmp_path, max_bytes=1024 * 1024), fake_repository,
                              fake_index.access(), tags=fake_tags)
    library.tracker.adopt_untracked()
    client = TestClient(api.create_app(settings_for(tmp_path), library=library), raise_server_exceptions=False)

    [document] = client.get("/documents").json()["documents"]
    assert document["source"] == "acme/1.0/guide/old.md"
    assert document["unlisted"] == ["product", "version", "doc_type"]
    assert document["tags"] == []


def test_list_filters_by_tag(tagged, fake_tags):
    workflow, search = fake_tags.value("module", "workflow").id, fake_tags.value("module", "search").id
    admins = fake_tags.value("audience", "admins").id
    a = post_tagged(tagged, filename="a.md", tags=[workflow, admins]).json()
    b = post_tagged(tagged, filename="b.md", version="24.2", tags=[search]).json()
    c = post_tagged(tagged, filename="c.md", version="24.2", doc_type="upgrade-guide").json()

    def ids(*filters):
        response = tagged.get("/documents", params={"tag": list(filters)})
        assert response.status_code == 200, response.text
        return {d["id"] for d in response.json()["documents"]}

    assert ids() == {a["id"], b["id"], c["id"]}
    assert ids("version:24.2") == {b["id"], c["id"]}
    assert ids("version:24.2", "version:24.4") == {a["id"], b["id"], c["id"]}  # any value within a category
    assert ids("version:24.2", "doc_type:release-notes") == {b["id"]}         # every category
    assert ids("module:workflow", "module:search") == {a["id"], b["id"]}
    assert ids("module:workflow", "audience:admins") == {a["id"]}
    assert ids("module:search", "version:24.4") == set()

    fake_tags.update_value(search, active=False)  # a retired value still filters
    assert ids("module:search") == {b["id"]}

    for bad in ("nope:x", "version", ":x", "version:"):
        response = tagged.get("/documents", params={"tag": bad})
        assert response.status_code == 422, bad
