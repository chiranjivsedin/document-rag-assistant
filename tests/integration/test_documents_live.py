"""Live check: the documents table and its repository against the local Docker PostgreSQL.

Needs `docker compose up -d` (Ollama is not used). Run with: pytest -m integration
"""

import os
import uuid

import psycopg
import pytest

from rag.dms.repository import DocumentRepository, DuplicateDocument, NewDocument

pytestmark = pytest.mark.integration

LOCAL_DB = "postgresql://rag:rag_local_dev@127.0.0.1:5433/rag"
TABLE = "it_documents"


@pytest.fixture
def db_url():
    url = os.environ.get("DATABASE_URL", LOCAL_DB)

    def drop():
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {TABLE}")

    drop()
    yield url
    drop()


@pytest.fixture
def repo(db_url):
    repo = DocumentRepository(db_url, table=TABLE)
    repo.create_table()
    return repo


def _new(name, **changes):
    fields = dict(source=f"documentum/16.2/release-notes/{name}", filename=name, content_type="application/pdf",
                  size_bytes=100, file_hash="b" * 64, product="documentum", version="16.2", doc_type="release-notes")
    return NewDocument(**{**fields, **changes})


def test_create_table_is_repeatable(repo):
    repo.create_table()
    assert repo.list_all() == []


def test_add_get_and_list(repo):
    first = repo.add(_new("a.pdf"))
    second = repo.add(_new("b.pdf"))

    assert first.status == "pending" and first.origin == "upload"
    assert first.error is None and first.chunk_count is None and first.indexed_at is None
    assert isinstance(first.id, uuid.UUID) and first.added_at is not None
    assert repo.get(first.id) == first
    assert repo.get_by_source(second.source) == second
    assert repo.get(uuid.uuid4()) is None
    assert {d.id for d in repo.list_all()} == {first.id, second.id}


def test_a_duplicate_source_is_rejected_and_the_first_row_kept(repo):
    original = repo.add(_new("a.pdf"))
    with pytest.raises(DuplicateDocument):
        repo.add(_new("a.pdf", filename="other name.pdf", size_bytes=999))
    assert repo.list_all() == [original]


def test_adopt_tracks_an_existing_file_once(repo):
    adopted = repo.adopt(_new("notes.md", source="documentum/16.2/notes.md", doc_type=None,
                              filename="notes.md", content_type="text/markdown"), chunk_count=4)
    assert adopted.origin == "folder" and adopted.status == "indexed"
    assert adopted.chunk_count == 4 and adopted.indexed_at is not None and adopted.doc_type is None

    assert repo.adopt(_new("notes.md", source="documentum/16.2/notes.md", doc_type=None,
                           filename="notes.md"), chunk_count=9) is None
    assert repo.list_all() == [adopted]  # unchanged


def test_adopt_never_takes_over_an_uploaded_document(repo):
    uploaded = repo.add(_new("a.pdf"))
    assert repo.adopt(_new("a.pdf"), chunk_count=3) is None
    assert repo.get(uploaded.id) == uploaded


def test_status_updates_only_touch_documents_claimed_by_the_run(repo):
    earlier = repo.adopt(_new("earlier.pdf"), chunk_count=5)  # indexed before this run
    ok = repo.add(_new("ok.pdf"))
    scanned = repo.add(_new("scanned.pdf"))

    claimed = repo.claim_pending()
    assert {d.id for d in claimed} == {ok.id, scanned.id}
    assert {d.status for d in claimed} == {"processing"}

    late = repo.add(_new("late.pdf"))  # uploaded while the run is going

    assert repo.mark_indexed(ok.id, chunk_count=3) is True
    # A failure reported for everything must only fail what this run claimed and hasn't finished.
    assert repo.mark_failed([earlier.id, ok.id, scanned.id, late.id], "No extractable text") == 1

    assert repo.get(earlier.id) == earlier
    assert (repo.get(ok.id).status, repo.get(ok.id).chunk_count) == ("indexed", 3)
    assert repo.get(ok.id).indexed_at is not None
    assert (repo.get(scanned.id).status, repo.get(scanned.id).error) == ("failed", "No extractable text")
    assert repo.get(late.id).status == "pending"
    assert repo.mark_indexed(earlier.id, chunk_count=1) is False  # not claimed: unchanged
    assert repo.get(earlier.id).chunk_count == 5


def test_release_processing_after_an_interrupted_run(repo):
    document = repo.add(_new("a.pdf"))
    repo.claim_pending()
    assert repo.release_processing() == 1
    assert repo.get(document.id).status == "pending"
    assert [d.id for d in repo.claim_pending()] == [document.id]


def test_delete_removes_the_row(repo):
    document = repo.add(_new("a.pdf"))
    assert repo.delete(document.id) is True
    assert repo.delete(document.id) is False
    assert repo.get(document.id) is None


@pytest.mark.parametrize("statement", [
    pytest.param("INSERT INTO {t} (source, filename, content_type, size_bytes, file_hash, origin) "
                 "VALUES ('x.pdf', 'x.pdf', 'application/pdf', 1, repeat('c', 64), 'upload')",
                 id="upload-without-folders"),
    pytest.param("INSERT INTO {t} (source, filename, content_type, size_bytes, file_hash, origin, status) "
                 "VALUES ('x.pdf', 'x.pdf', 'application/pdf', 1, repeat('c', 64), 'folder', 'done')",
                 id="unknown-status"),
    pytest.param("INSERT INTO {t} (source, filename, content_type, size_bytes, file_hash, origin, status) "
                 "VALUES ('x.pdf', 'x.pdf', 'application/pdf', 1, repeat('c', 64), 'folder', 'failed')",
                 id="failed-without-reason"),
    pytest.param("INSERT INTO {t} (source, filename, content_type, size_bytes, file_hash, origin, status) "
                 "VALUES ('x.pdf', 'x.pdf', 'application/pdf', 1, repeat('c', 64), 'folder', 'indexed')",
                 id="indexed-without-time"),
    pytest.param("INSERT INTO {t} (source, filename, content_type, size_bytes, file_hash, origin) "
                 "VALUES ('x.pdf', 'x.pdf', 'application/pdf', 1, 'abc', 'folder')",
                 id="hash-not-sha256"),
])
def test_the_table_rejects_invalid_rows_itself(repo, db_url, statement):
    with psycopg.connect(db_url) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(statement.format(t=TABLE))
