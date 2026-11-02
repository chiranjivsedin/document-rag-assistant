import io
import uuid

import psycopg
import pytest

from rag.dms.library import (
    DeletionFailed,
    DeletionSteps,
    DocumentExists,
    DocumentLibrary,
    DocumentNotFound,
    FileMissing,
    StorageFailed,
)
from rag.dms.repository import DuplicateDocument
from rag.dms.storage import DocumentStorage, DocumentTooLarge, InvalidDocument

MB = 1024 * 1024
SOURCE = "documentum/16.2/release-notes/notes.md"


@pytest.fixture
def library(tmp_path, fake_repository, fake_index):
    return DocumentLibrary(DocumentStorage(tmp_path, max_bytes=MB), fake_repository, fake_index.access())


def upload(library, data=b"# Webtop is deprecated", filename="notes.md",
           product="documentum", version="16.2", doc_type="release-notes"):
    return library.upload(io.BytesIO(data), filename, product, version, doc_type)


def files_in(library):
    root = library.storage.documents_dir
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


# Upload

def test_upload_stores_the_file_and_records_it_as_pending(library, fake_repository):
    document = upload(library, filename="C:\\fakepath\\Release Notes.md")

    assert document.status == "pending" and document.origin == "upload"
    assert document.source == "documentum/16.2/release-notes/Release Notes.md"
    assert document.filename == "Release Notes.md"
    assert (document.product, document.version, document.doc_type) == ("documentum", "16.2", "release-notes")
    assert document.content_type == "text/markdown" and document.size_bytes == 22
    assert files_in(library) == ["documentum/16.2/release-notes/Release Notes.md"]
    assert list(fake_repository.rows) == [document.id]


def test_upload_keeps_the_original_name_for_display(library):
    document = upload(library, filename="Résumé (v2).md")
    assert document.filename == "Résumé (v2).md"
    assert document.source.endswith("/Resume (v2).md")


def test_upload_follows_existing_folder_capitals(library):
    (library.storage.documents_dir / "Documentum").mkdir()
    document = upload(library)
    assert document.source == "Documentum/16.2/release-notes/notes.md"
    assert document.product == "Documentum"  # matches what ingestion will store for its chunks


@pytest.mark.parametrize("changes, message", [
    ({"product": ""}, "product is required"),
    ({"version": "../16.2"}, "version must be"),
    ({"doc_type": "release notes"}, "doc_type must be"),
    ({"filename": "notes.docx"}, "Only .md, .pdf, .txt"),
    ({"data": b""}, "empty"),
    ({"data": b"%PDF-1.4 broken", "filename": "notes.pdf"}, "can't be read"),
])
def test_invalid_uploads_leave_nothing_behind(library, fake_repository, changes, message):
    with pytest.raises(InvalidDocument, match=message):
        upload(library, **changes)
    assert files_in(library) == []
    assert fake_repository.rows == {}


def test_too_large_upload(library):
    with pytest.raises(DocumentTooLarge):
        upload(library, data=b"x" * (MB + 1))
    assert files_in(library) == []


def test_upload_to_a_tracked_path_is_a_duplicate(library):
    first = upload(library)
    library.storage.delete(first.source)  # even if its file has gone missing, the path stays taken
    with pytest.raises(DocumentExists, match=SOURCE):
        upload(library, data=b"other")
    assert files_in(library) == []


def test_upload_never_replaces_an_untracked_file(library, fake_repository):
    path = library.storage.documents_dir / SOURCE
    path.parent.mkdir(parents=True)
    path.write_bytes(b"put there by hand")
    with pytest.raises(DocumentExists):
        upload(library)
    assert path.read_bytes() == b"put there by hand"
    assert fake_repository.rows == {}


def test_a_failed_insert_removes_the_new_file(library, fake_repository):
    fake_repository.fail_on["add"] = psycopg.OperationalError("connection lost")
    with pytest.raises(psycopg.OperationalError):
        upload(library)
    assert files_in(library) == []  # no unrecorded file is left in the documents folder


def test_a_racing_duplicate_insert_removes_the_new_file(library, fake_repository):
    fake_repository.fail_on["add"] = DuplicateDocument(SOURCE)
    with pytest.raises(DocumentExists):
        upload(library)
    assert files_in(library) == []


def test_a_failed_cleanup_still_reports_the_original_error(library, fake_repository, monkeypatch, caplog):
    fake_repository.fail_on["add"] = psycopg.OperationalError("connection lost")
    monkeypatch.setattr(library.storage, "delete", lambda source: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(psycopg.OperationalError):
        upload(library)
    assert "delete it by hand" in caplog.text


def test_a_file_system_error_while_saving(library, monkeypatch):
    monkeypatch.setattr(library.storage, "save", lambda stream, source: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(StorageFailed, match="couldn't be stored"):
        upload(library)


# Reading

def test_list_get_and_original_file(library):
    first = upload(library, filename="a.md")
    second = upload(library, filename="b.md")

    assert [d.id for d in library.list()] == [second.id, first.id]
    assert library.get(first.id) == first
    document, path = library.original_file(first.id)
    assert document == first and path.read_bytes() == b"# Webtop is deprecated"


def test_unknown_document(library):
    with pytest.raises(DocumentNotFound):
        library.get(uuid.uuid4())
    with pytest.raises(DocumentNotFound):
        library.original_file(uuid.uuid4())


def test_original_file_missing_on_disk(library):
    document = upload(library)
    library.storage.delete(document.source)
    with pytest.raises(FileMissing):
        library.original_file(document.id)


# Deleting

def test_delete_removes_file_then_chunks_then_record(library, fake_repository, fake_index):
    document = upload(library)
    assert library.delete(document.id) == document
    assert files_in(library) == []
    assert fake_index.deleted == [document.source]
    assert fake_repository.rows == {}


def test_delete_when_the_file_is_already_gone(library, fake_repository, fake_index):
    document = upload(library)
    library.storage.delete(document.source)
    library.delete(document.id)
    assert fake_index.deleted == [document.source] and fake_repository.rows == {}


def test_delete_unknown_document(library):
    with pytest.raises(DocumentNotFound):
        library.delete(uuid.uuid4())


def test_a_locked_file_stops_the_deletion_before_anything_changes(library, fake_repository, fake_index,
                                                                  monkeypatch):
    document = upload(library)
    monkeypatch.setattr(library.storage, "delete", lambda source: (_ for _ in ()).throw(PermissionError("in use")))

    with pytest.raises(DeletionFailed) as raised:
        library.delete(document.id)

    assert raised.value.steps == DeletionSteps()
    assert isinstance(raised.value.__cause__, PermissionError)
    assert files_in(library) == [document.source]
    assert fake_index.deleted == [] and document.id in fake_repository.rows


def test_failed_chunk_removal_keeps_the_record_and_a_retry_finishes(library, fake_repository, fake_index):
    document = upload(library)
    fake_index.error = psycopg.OperationalError("database down")

    with pytest.raises(DeletionFailed) as raised:
        library.delete(document.id)
    assert raised.value.steps == DeletionSteps(file_deleted=True)
    assert files_in(library) == [] and document.id in fake_repository.rows  # still listed, so it can be retried

    fake_index.error = None
    library.delete(document.id)
    assert fake_index.deleted == [document.source] and fake_repository.rows == {}


def test_failed_record_removal_and_a_retry_finishes(library, fake_repository, fake_index):
    document = upload(library)
    fake_repository.fail_on["delete"] = psycopg.OperationalError("database down")

    with pytest.raises(DeletionFailed) as raised:
        library.delete(document.id)
    assert raised.value.steps == DeletionSteps(file_deleted=True, chunks_deleted=True)

    del fake_repository.fail_on["delete"]
    library.delete(document.id)
    assert fake_repository.rows == {}
    assert fake_index.deleted == [document.source, document.source]  # repeating a step is harmless
