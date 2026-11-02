"""Shared fixtures: in-memory stand-ins for the document library's database parts (library and API tests)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from rag.dms.repository import DocumentRecord, DuplicateDocument, NewDocument


class FakeRepository:
    """Behaves like DocumentRepository for add/get/list/delete; `fail_on` makes a method raise."""

    def __init__(self):
        self.rows: dict[uuid.UUID, DocumentRecord] = {}
        self.fail_on: dict[str, BaseException] = {}
        self._clock = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)

    def _maybe_fail(self, method):
        if method in self.fail_on:
            raise self.fail_on[method]

    def add(self, new: NewDocument) -> DocumentRecord:
        self._maybe_fail("add")
        if self.get_by_source(new.source):
            raise DuplicateDocument(new.source)
        self._clock += timedelta(minutes=1)
        record = DocumentRecord(
            id=uuid.uuid4(), source=new.source, filename=new.filename, product=new.product, version=new.version,
            doc_type=new.doc_type, content_type=new.content_type, size_bytes=new.size_bytes,
            file_hash=new.file_hash, origin="upload", status="pending", error=None, chunk_count=None,
            added_at=self._clock, indexed_at=None,
        )
        self.rows[record.id] = record
        return record

    def get(self, document_id):
        self._maybe_fail("get")
        return self.rows.get(document_id)

    def get_by_source(self, source):
        self._maybe_fail("get_by_source")
        return next((r for r in self.rows.values() if r.source == source), None)

    def list_all(self):
        self._maybe_fail("list_all")
        return sorted(self.rows.values(), key=lambda r: r.added_at, reverse=True)

    def delete(self, document_id) -> bool:
        self._maybe_fail("delete")
        return self.rows.pop(document_id, None) is not None


class FakeChunks:
    """Stands in for database.delete_chunks: records each source; `error` makes it raise."""

    def __init__(self):
        self.deleted: list[str] = []
        self.error: BaseException | None = None

    def __call__(self, source: str) -> int:
        if self.error is not None:
            raise self.error
        self.deleted.append(source)
        return 3


@pytest.fixture
def fake_repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def fake_chunks() -> FakeChunks:
    return FakeChunks()
