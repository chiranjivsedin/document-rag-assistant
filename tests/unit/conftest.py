"""Shared fixtures: in-memory stand-ins for the document library's database parts (library and API tests)."""

import dataclasses
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from rag.dms.index import IndexAccess
from rag.dms.repository import DocumentRecord, DuplicateDocument, NewDocument
from rag.ingestion.loaders import file_metadata, iter_files
from rag.ingestion.pipeline import IngestReport


class FakeRepository:
    """Behaves like DocumentRepository, including which rows each status change may touch.
    `fail_on` makes a method raise."""

    def __init__(self):
        self.rows: dict[uuid.UUID, DocumentRecord] = {}
        self.fail_on: dict[str, BaseException] = {}
        self._clock = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)

    def _maybe_fail(self, method):
        if method in self.fail_on:
            raise self.fail_on[method]

    def _insert(self, new: NewDocument, origin, status, chunk_count=None, error=None) -> DocumentRecord:
        self._clock += timedelta(minutes=1)
        record = DocumentRecord(
            id=uuid.uuid4(), source=new.source, filename=new.filename, product=new.product, version=new.version,
            doc_type=new.doc_type, content_type=new.content_type, size_bytes=new.size_bytes,
            file_hash=new.file_hash, origin=origin, status=status, error=error, chunk_count=chunk_count,
            added_at=self._clock, indexed_at=self._clock if status == "indexed" else None,
        )
        self.rows[record.id] = record
        return record

    def _update(self, document_id, **changes):
        self.rows[document_id] = dataclasses.replace(self.rows[document_id], **changes)

    def add(self, new: NewDocument) -> DocumentRecord:
        self._maybe_fail("add")
        if self.get_by_source(new.source):
            raise DuplicateDocument(new.source)
        return self._insert(new, "upload", "pending")

    def adopt(self, new: NewDocument, chunk_count=None, error=None):
        self._maybe_fail("adopt")
        if self.get_by_source(new.source):
            return None
        status = "failed" if error else ("pending" if chunk_count is None else "indexed")
        return self._insert(new, "folder", status, chunk_count=chunk_count if status == "indexed" else None,
                            error=error)

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

    def claim_for_indexing(self):
        self._maybe_fail("claim_for_indexing")
        claimed = [r.id for r in self.rows.values() if r.status in ("pending", "failed")]
        for document_id in claimed:
            self._update(document_id, status="processing", error=None)
        return [self.rows[document_id] for document_id in claimed]

    def mark_indexed(self, document_id, chunk_count) -> bool:
        self._maybe_fail("mark_indexed")
        row = self.rows.get(document_id)
        if row is None or row.status != "processing":
            return False
        self._update(document_id, status="indexed", error=None, chunk_count=chunk_count, indexed_at=self._clock)
        return True

    def mark_failed(self, document_ids, error) -> int:
        self._maybe_fail("mark_failed")
        changed = [i for i in document_ids if i in self.rows and self.rows[i].status == "processing"]
        for document_id in changed:
            self._update(document_id, status="failed", error=error)
        return len(changed)

    def release_processing(self) -> int:
        self._maybe_fail("release_processing")
        changed = [i for i, r in self.rows.items() if r.status == "processing"]
        for document_id in changed:
            self._update(document_id, status="pending")
        return len(changed)

    def count_pending(self) -> int:
        self._maybe_fail("count_pending")
        return sum(r.status == "pending" for r in self.rows.values())

    def refresh_indexed(self, source, file_hash, size_bytes, chunk_count) -> bool:
        row = self.get_by_source(source)
        if row is None or row.status != "indexed":
            return False
        self._update(row.id, file_hash=file_hash, size_bytes=size_bytes, chunk_count=chunk_count)
        return True

    def mark_missing(self, sources, error) -> int:
        changed = [r.id for r in self.rows.values() if r.source in set(sources) and r.status == "indexed"]
        for document_id in changed:
            self._update(document_id, status="failed", error=error)
        return len(changed)

    def status_of(self, source) -> str | None:
        row = self.get_by_source(source)
        return row.status if row else None


class FakeIndex:
    """Stands in for the chunks table behind IndexAccess. `chunks` maps source -> (file_hash, count);
    `error` makes delete_chunks raise."""

    def __init__(self):
        self.chunks: dict[str, tuple[str, int]] = {}
        self.deleted: list[str] = []
        self.error: BaseException | None = None

    def delete_chunks(self, source: str) -> int:
        if self.error is not None:
            raise self.error
        self.deleted.append(source)
        return self.chunks.pop(source, ("", 3))[1]

    def access(self) -> IndexAccess:
        return IndexAccess(
            delete_chunks=self.delete_chunks,
            count_chunks=lambda sources: {s: self.chunks[s][1] for s in sources if s in self.chunks},
            indexed_files=lambda: {s: (h, "nomic-embed-text;chunks=v2/1000/150") for s, (h, _) in self.chunks.items()},
            fingerprint="nomic-embed-text;chunks=v2/1000/150",
        )


class FakePipeline:
    """Stands in for the ingestion run (service.run_ingest): brings a FakeIndex in line with the folder and
    reports added / updated / unchanged / removed / no_text like the real pipeline. Files with "scan" in
    their name have no text.

    `during()` runs mid-run (e.g. an upload), `gate` holds the run until set, `error` makes it fail.
    `max_running` records how many runs ever overlapped.
    """

    def __init__(self, index: FakeIndex):
        self.index = index
        self.calls = 0
        self.running = 0
        self.max_running = 0
        self.during = None
        self.gate: threading.Event | None = None
        self.error: BaseException | None = None
        self._lock = threading.Lock()

    def __call__(self, services, documents_dir) -> IngestReport:  # the signature of service.run_ingest
        return self.run(documents_dir)

    def run(self, documents_dir) -> IngestReport:
        with self._lock:
            self.calls += 1
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            if self.during:
                self.during()
            if self.gate is not None:
                assert self.gate.wait(5), "the test never released the run"
            if self.error is not None:
                raise self.error
            return self._sync(documents_dir)
        finally:
            with self._lock:
                self.running -= 1

    def _sync(self, documents_dir) -> IngestReport:
        report, on_disk = IngestReport(), set()
        for path in iter_files(documents_dir):
            meta = file_metadata(path, documents_dir)
            source = meta["source"]
            on_disk.add(source)
            previous = self.index.chunks.get(source)
            if previous and previous[0] == meta["file_hash"]:
                report.unchanged.append(source)
            elif "scan" in path.name:
                self.index.chunks.pop(source, None)
                report.no_text.append(source)
            else:
                self.index.chunks[source] = (meta["file_hash"], 2)
                (report.updated if previous else report.added).append(source)
                report.chunks_stored += 2
        for source in sorted(set(self.index.chunks) - on_disk):
            del self.index.chunks[source]
            report.removed.append(source)
        return report


@pytest.fixture
def fake_repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def fake_index() -> FakeIndex:
    return FakeIndex()


@pytest.fixture
def fake_pipeline(fake_index) -> FakePipeline:
    return FakePipeline(fake_index)
