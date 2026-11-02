import uuid
from datetime import datetime, timezone

import pytest

from rag.dms import repository as repository_module
from rag.dms.repository import DocumentRecord, DocumentRepository, DuplicateDocument, NewDocument

HASH = "a" * 64


def _new(**changes) -> NewDocument:
    fields = dict(
        source="documentum/16.2/release-notes/notes.pdf", filename="Notes (final).pdf",
        content_type="application/pdf", size_bytes=1234, file_hash=HASH,
        product="documentum", version="16.2", doc_type="release-notes",
    )
    return NewDocument(**{**fields, **changes})


def _row(**changes) -> dict:
    row = dict(
        id=uuid.uuid4(), source="documentum/16.2/release-notes/notes.pdf", filename="notes.pdf",
        product="documentum", version="16.2", doc_type="release-notes", content_type="application/pdf",
        size_bytes=1234, file_hash=HASH, origin="upload", status="pending", error=None, chunk_count=None,
        added_at=datetime(2026, 11, 2, tzinfo=timezone.utc), indexed_at=None,
    )
    return {**row, **changes}


class FakeDatabase:
    """Stands in for database.connect: records each statement and returns canned rows / row counts."""

    def __init__(self, rows=(), rowcount=0):
        self.rows = list(rows)
        self.rowcount = rowcount
        self.statements: list[tuple[str, tuple]] = []

    def connect(self, url):
        return _FakeConnection(self)


class _FakeConnection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, row_factory=None):
        return _FakeCursor(self.db)

    def execute(self, query, params=()):
        self.db.statements.append((query.as_string(None), params))
        return type("Result", (), {"rowcount": self.db.rowcount})()


class _FakeCursor(_FakeConnection):
    def execute(self, query, params=()):
        self.db.statements.append((query.as_string(None), params))

    def fetchall(self):
        return self.db.rows


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeDatabase()
    monkeypatch.setattr(repository_module.database, "connect", db.connect)
    return db


@pytest.fixture
def no_db(monkeypatch):
    def refuse(url):
        raise AssertionError("should not touch the database")

    monkeypatch.setattr(repository_module.database, "connect", refuse)


# NewDocument validation: rows must match the chunk metadata the loaders derive from the same path.

def test_a_valid_upload_document():
    new = _new()
    assert new.source == "documentum/16.2/release-notes/notes.pdf"
    assert new.filename == "Notes (final).pdf"  # the display name may differ from the stored file name


def test_an_adopted_file_higher_up_has_no_doc_type():
    new = _new(source="documentum/16.2/notes.md", doc_type=None, content_type="text/markdown")
    assert (new.product, new.version, new.doc_type) == ("documentum", "16.2", None)


def test_a_file_at_the_top_has_no_folders():
    new = _new(source="readme.txt", product=None, version=None, doc_type=None)
    assert new.product is None


@pytest.mark.parametrize("source", [
    "", "/documentum/16.2/release-notes/notes.pdf", "documentum\\16.2\\notes.pdf", "C:/docs/notes.pdf",
    "documentum/../notes.pdf", "documentum/./16.2/notes.pdf", "documentum//notes.pdf", "documentum/16.2/",
])
def test_rejects_sources_that_are_not_plain_relative_paths(source):
    with pytest.raises(ValueError, match="relative path"):
        _new(source=source)


@pytest.mark.parametrize("changes", [
    {"product": "other"},
    {"version": "23.4"},
    {"doc_type": None},
    {"source": "documentum/16.2/notes.pdf"},  # doc_type given, but the file has no third folder
])
def test_folder_values_must_match_the_source(changes):
    with pytest.raises(ValueError, match="must match the folders"):
        _new(**changes)


@pytest.mark.parametrize("changes, message", [
    ({"filename": ""}, "filename"),
    ({"filename": "a/b.pdf"}, "filename"),
    ({"filename": "a\\b.pdf"}, "filename"),
    ({"content_type": ""}, "content_type"),
    ({"size_bytes": -1}, "size_bytes"),
    ({"file_hash": "A" * 64}, "file_hash"),
    ({"file_hash": "a" * 63}, "file_hash"),
])
def test_rejects_invalid_fields(changes, message):
    with pytest.raises(ValueError, match=message):
        _new(**changes)


def test_table_name_must_be_a_plain_identifier():
    with pytest.raises(ValueError, match="plain table name"):
        DocumentRepository("postgresql://u:p@h/db", table="documents; DROP TABLE chunks")


# Repository behaviour that doesn't depend on PostgreSQL itself (the live test covers the SQL).

def test_an_upload_needs_product_version_and_doc_type(no_db):
    adopted_style = _new(source="readme.txt", product=None, version=None, doc_type=None)
    with pytest.raises(ValueError, match="needs a product, version and doc_type"):
        DocumentRepository("postgresql://u:p@h/db").add(adopted_style)


def test_add_inserts_an_upload_as_pending(fake_db):
    fake_db.rows = [_row()]
    record = DocumentRepository("postgresql://u:p@h/db", table="docs").add(_new())

    assert isinstance(record, DocumentRecord) and record.status == "pending"
    query, params = fake_db.statements[0]
    assert query.startswith('INSERT INTO "docs"')
    assert "ON CONFLICT (source) DO NOTHING" in query
    assert params[-4:] == ("upload", "pending", None, None)  # origin, status, chunk_count, error


def test_add_reports_a_duplicate_source(fake_db):
    fake_db.rows = []  # ON CONFLICT DO NOTHING returned no row
    with pytest.raises(DuplicateDocument) as raised:
        DocumentRepository("postgresql://u:p@h/db").add(_new())
    assert raised.value.source == "documentum/16.2/release-notes/notes.pdf"


def test_adopt_records_the_file_where_it_is_as_indexed(fake_db):
    fake_db.rows = [_row(origin="folder", status="indexed", chunk_count=7,
                         indexed_at=datetime(2026, 11, 2, tzinfo=timezone.utc))]
    record = DocumentRepository("postgresql://u:p@h/db").adopt(_new(), chunk_count=7)

    assert record.origin == "folder" and record.status == "indexed"
    query, params = fake_db.statements[0]
    assert "now())" in query  # indexed_at is set
    assert params[0] == "documentum/16.2/release-notes/notes.pdf"  # the existing path, not a copy
    assert params[-4:] == ("folder", "indexed", 7, None)


def test_adopt_returns_none_when_already_tracked(fake_db):
    fake_db.rows = []
    assert DocumentRepository("postgresql://u:p@h/db").adopt(_new(), chunk_count=7) is None


def test_adopt_a_file_not_indexed_yet_as_pending(fake_db):
    fake_db.rows = [_row(origin="folder")]
    DocumentRepository("postgresql://u:p@h/db").adopt(_new())
    query, params = fake_db.statements[0]
    assert "NULL)" in query  # no indexed_at
    assert params[-4:] == ("folder", "pending", None, None)


def test_adopt_a_file_without_text_as_failed(fake_db):
    fake_db.rows = [_row(origin="folder", status="failed", error="No text")]
    DocumentRepository("postgresql://u:p@h/db").adopt(_new(), error="No text")
    _, params = fake_db.statements[0]
    assert params[-4:] == ("folder", "failed", None, "No text")


def test_claim_takes_pending_and_failed_documents_and_clears_the_error(fake_db):
    DocumentRepository("postgresql://u:p@h/db").claim_for_indexing()
    query, _ = fake_db.statements[0]
    assert "SET status = 'processing', error = NULL WHERE status IN ('pending', 'failed')" in query


def test_refresh_only_touches_an_indexed_document(fake_db):
    fake_db.rowcount = 1
    assert DocumentRepository("postgresql://u:p@h/db").refresh_indexed("a/b.md", HASH, 10, 4) is True
    query, params = fake_db.statements[0]
    assert "WHERE source = %s AND status = 'indexed'" in query
    assert params == (HASH, 10, 4, "a/b.md")


def test_mark_missing_only_touches_indexed_documents(fake_db):
    fake_db.rowcount = 1
    assert DocumentRepository("postgresql://u:p@h/db").mark_missing(["a/b.md"], "gone") == 1
    query, params = fake_db.statements[0]
    assert "WHERE source = ANY(%s) AND status = 'indexed'" in query
    assert params == ("gone", ["a/b.md"])


def test_mark_missing_with_no_sources_does_nothing(no_db):
    assert DocumentRepository("postgresql://u:p@h/db").mark_missing([], "gone") == 0


def test_mark_indexed_only_finishes_a_claimed_document(fake_db):
    document_id = uuid.uuid4()
    fake_db.rowcount = 0  # e.g. the document was indexed earlier, not claimed by this run
    assert DocumentRepository("postgresql://u:p@h/db").mark_indexed(document_id, chunk_count=3) is False
    query, params = fake_db.statements[0]
    assert "WHERE id = %s AND status = 'processing'" in query
    assert params == (3, document_id)


def test_mark_failed_only_touches_claimed_documents(fake_db):
    ids = [uuid.uuid4(), uuid.uuid4()]
    fake_db.rowcount = 1
    assert DocumentRepository("postgresql://u:p@h/db").mark_failed(ids, "No extractable text") == 1
    query, params = fake_db.statements[0]
    assert "WHERE id = ANY(%s) AND status = 'processing'" in query
    assert params == ("No extractable text", ids)


def test_mark_failed_needs_a_reason(no_db):
    with pytest.raises(ValueError, match="reason"):
        DocumentRepository("postgresql://u:p@h/db").mark_failed([uuid.uuid4()], "")


def test_mark_failed_with_no_documents_does_nothing(no_db):
    assert DocumentRepository("postgresql://u:p@h/db").mark_failed([], "boom") == 0


def test_release_processing_returns_interrupted_documents_to_pending(fake_db):
    fake_db.rowcount = 2
    assert DocumentRepository("postgresql://u:p@h/db").release_processing() == 2
    query, _ = fake_db.statements[0]
    assert "SET status = 'pending' WHERE status = 'processing'" in query


def test_delete_removes_only_the_row(fake_db):
    document_id = uuid.uuid4()
    fake_db.rowcount = 1
    assert DocumentRepository("postgresql://u:p@h/db").delete(document_id) is True
    assert fake_db.statements == [('DELETE FROM "documents" WHERE id = %s', (document_id,))]


def test_create_table_defines_the_constraints(fake_db):
    DocumentRepository("postgresql://u:p@h/db").create_table()
    query, _ = fake_db.statements[0]
    assert query.lstrip().startswith('CREATE TABLE IF NOT EXISTS "documents"')
    assert "source       text NOT NULL UNIQUE" in query
    assert "CHECK (status IN ('pending', 'processing', 'indexed', 'failed'))" in query
    assert "CHECK (origin = 'folder' OR (product IS NOT NULL" in query
    assert "'^[0-9a-f]{64}$'" in query  # the doubled braces became literal ones
