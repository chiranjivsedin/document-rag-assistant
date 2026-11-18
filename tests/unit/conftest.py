"""Shared fixtures: in-memory stand-ins for the document library's database parts (library and API tests)."""

import dataclasses
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from rag.dms.index import IndexAccess
from rag.dms.repository import DocumentRecord, DuplicateDocument, NewDocument
from rag.dms.tags import (
    BUILTIN_KEYS,
    BUILTIN_LABELS,
    SEED_VALUES,
    TagCategory,
    TagConflict,
    TagInUse,
    TagNotFound,
    TagValue,
    check_category_key,
    check_code,
    check_label,
)
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


class FakeTagRepository:
    """Behaves like TagRepository (seeded), in memory. `documents` (a FakeRepository) provides the documents that
    tags and in-use checks refer to; `used_in_index` holds (category_key, code) pairs a chunk still uses.
    `calls` records each change; `fail_on` makes a method raise."""

    def __init__(self, documents: FakeRepository | None = None):
        self.documents = documents
        self.cats: list[dict] = []
        self.values: dict[int, TagValue] = {}
        self.doc_tags: dict[uuid.UUID, list[int]] = {}
        self.used_in_index: set[tuple[str, str]] = set()
        self.calls: list[tuple] = []
        self.fail_on: dict[str, BaseException] = {}
        self._next_id = 1
        for key in BUILTIN_KEYS:
            self.cats.append({"key": key, "label": BUILTIN_LABELS[key], "builtin": True, "multiple": False})
            for code, label in SEED_VALUES[key]:
                self._new_value(key, code, label)

    def _maybe_fail(self, method):
        if method in self.fail_on:
            raise self.fail_on[method]

    def _new_value(self, key, code, label) -> TagValue:
        value = TagValue(id=self._next_id, category_key=key, code=code, label=label, active=True,
                         sort_order=sum(v.category_key == key for v in self.values.values()))
        self.values[value.id] = value
        self._next_id += 1
        return value

    def _category(self, key):
        return next((c for c in self.cats if c["key"] == key), None)

    def categories(self) -> list[TagCategory]:
        self._maybe_fail("categories")
        return [TagCategory(key=c["key"], label=c["label"], builtin=c["builtin"], multiple=c["multiple"],
                            values=sorted((v for v in self.values.values() if v.category_key == c["key"]),
                                          key=lambda v: (v.sort_order, v.label.lower())))
                for c in self.cats]

    def is_active_code(self, category_key, code) -> bool:
        self._maybe_fail("is_active_code")
        return any(v.category_key == category_key and v.code == code and v.active for v in self.values.values())

    def add_category(self, key, label, multiple) -> TagCategory:
        self._maybe_fail("add_category")
        self.calls.append(("add_category", key, label, multiple))
        key, label = check_category_key(key), check_label(label)
        if any(c["key"] == key or c["label"].lower() == label.lower() for c in self.cats):
            raise TagConflict(f"A category with the key '{key}' or label '{label}' already exists.")
        self.cats.append({"key": key, "label": label, "builtin": False, "multiple": multiple})
        return TagCategory(key=key, label=label, builtin=False, multiple=multiple, values=[])

    def add_value(self, category_key, code, label) -> TagValue:
        self._maybe_fail("add_value")
        self.calls.append(("add_value", category_key, code, label))
        code, label = check_code(code), check_label(label)
        category = self._category(category_key)
        if category is None:
            raise TagNotFound(f"Unknown tag category '{category_key}'.")
        for v in self.values.values():
            if v.category_key == category_key and (v.code.lower() == code.lower()
                                                   or (v.active and v.label.lower() == label.lower())):
                raise TagConflict(f"{category['label']} already has the code '{code}' or label '{label}'.")
        return self._new_value(category_key, code, label)

    def update_value(self, value_id, label=None, active=None, sort_order=None) -> TagValue:
        self._maybe_fail("update_value")
        self.calls.append(("update_value", value_id, label, active, sort_order))
        if value_id not in self.values:
            raise TagNotFound(f"Unknown tag value {value_id}.")
        changes = {}
        if label is not None:
            changes["label"] = check_label(label)
        if active is not None:
            changes["active"] = active
        if sort_order is not None:
            changes["sort_order"] = sort_order
        updated = dataclasses.replace(self.values[value_id], **changes)
        if updated.active and any(v.id != value_id and v.category_key == updated.category_key and v.active
                                  and v.label.lower() == updated.label.lower() for v in self.values.values()):
            raise TagConflict(f"An active value labelled '{updated.label}' already exists.")
        self.values[value_id] = updated
        return updated

    def delete_value(self, value_id) -> None:
        self._maybe_fail("delete_value")
        self.calls.append(("delete_value", value_id))
        value = self.values.get(value_id)
        if value is None:
            raise TagNotFound(f"Unknown tag value {value_id}.")
        used = any(value_id in ids for ids in self.doc_tags.values())
        used = used or (value.category_key, value.code) in self.used_in_index
        if self.documents is not None and value.category_key in BUILTIN_KEYS:
            used = used or any(getattr(r, value.category_key) == value.code for r in self.documents.rows.values())
        if used:
            raise TagInUse(f"'{value.label}' is in use, so it can only be retired.")
        del self.values[value_id]

    def document_tags(self, document_ids) -> dict:
        self._maybe_fail("document_tags")
        return {d: [self.values[i] for i in self.doc_tags[d]] for d in document_ids if self.doc_tags.get(d)}

    def set_document_tags(self, document_id, value_ids) -> list[TagValue]:
        self._maybe_fail("set_document_tags")
        self.calls.append(("set_document_tags", document_id, list(value_ids)))
        if self.documents is not None and document_id not in self.documents.rows:
            raise TagNotFound("Unknown document.")
        wanted = list(dict.fromkeys(value_ids))
        per_category: dict[str, int] = {}
        for value_id in wanted:
            value = self.values.get(value_id)
            if value is None:
                raise TagNotFound(f"Unknown tag value {value_id}.")
            category = self._category(value.category_key)
            if category["builtin"]:
                raise TagConflict(f"{category['label']} comes from the document's folder; it isn't set as a tag.")
            if not value.active and value_id not in self.doc_tags.get(document_id, []):
                raise TagConflict(f"'{value.label}' is retired and can't be added.")
            per_category[value.category_key] = per_category.get(value.category_key, 0) + 1
            if not category["multiple"] and per_category[value.category_key] > 1:
                raise TagConflict(f"{category['label']} takes one value only.")
        self.doc_tags[document_id] = wanted
        return [self.values[i] for i in wanted]

    def sources_with_tags(self, selected) -> set[str]:
        self._maybe_fail("sources_with_tags")
        self.calls.append(("sources_with_tags", {k: list(v) for k, v in selected.items()}))
        selected = {key: set(codes) for key, codes in selected.items() if codes}
        for key in selected:
            category = self._category(key)
            if category is None or category["builtin"]:
                raise TagNotFound(f"Unknown tag category '{key}'.")
        rows = self.documents.rows.values() if self.documents is not None else []
        result = set()
        for row in rows:
            codes = {(self.values[i].category_key, self.values[i].code) for i in self.doc_tags.get(row.id, [])}
            if all(any((key, code) in codes for code in wanted) for key, wanted in selected.items()):
                result.add(row.source)
        return result

    def value(self, key, code) -> TagValue:
        """Test helper: the value with this code."""
        return next(v for v in self.values.values() if v.category_key == key and v.code == code)


@pytest.fixture
def fake_tags(fake_repository) -> FakeTagRepository:
    return FakeTagRepository(fake_repository)


@pytest.fixture
def fake_repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def fake_index() -> FakeIndex:
    return FakeIndex()


@pytest.fixture
def fake_pipeline(fake_index) -> FakePipeline:
    return FakePipeline(fake_index)
