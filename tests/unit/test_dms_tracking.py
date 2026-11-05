import hashlib
import io
import os
import time
from pathlib import Path

import psycopg
import pytest

from rag.dms.library import DocumentLibrary
from rag.dms.storage import DocumentStorage
from rag.dms.tracking import FILE_MISSING, NO_TEXT
from rag.ingestion.loaders import DocumentsFolderUnreadable

MB = 1024 * 1024
OUTAGE = "Ollama or the database stopped responding. Check GET /health."


@pytest.fixture
def library(tmp_path, fake_repository, fake_index):
    return DocumentLibrary(DocumentStorage(tmp_path, max_bytes=MB), fake_repository, fake_index.access())


def put(library, source, text="text") -> str:
    """A file placed in the documents folder by hand."""
    path = library.storage.documents_dir / source
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode()).hexdigest()


def upload(library, name="notes.md", text="uploaded"):
    return library.upload(io.BytesIO(text.encode()), name, "acme", "1.0", "notes")


def run(library, fake_pipeline):
    return library.tracker.run(lambda: fake_pipeline.run(library.storage.documents_dir),
                               failure_message=lambda error: OUTAGE)


# Adopting files that are already in the documents folder

def test_adopt_records_existing_files_where_they_are(library, fake_repository, fake_index):
    current = put(library, "acme/1.0/notes/indexed.md", "indexed text")
    fake_index.chunks["acme/1.0/notes/indexed.md"] = (current, 5)
    put(library, "acme/1.0/notes/new.md")
    fake_index.chunks["acme/1.0/notes/changed.md"] = ("old-hash", 4)
    put(library, "acme/1.0/notes/changed.md", "edited since it was indexed")
    put(library, "acme/readme.txt")  # higher up the tree: no version or doc_type
    put(library, ".uploads/half-written.md")  # hidden: never a document
    put(library, "acme/1.0/notes/picture.png")  # unsupported type

    assert library.tracker.adopt_untracked() == 4

    rows = {r.source: r for r in fake_repository.rows.values()}
    assert set(rows) == {"acme/1.0/notes/indexed.md", "acme/1.0/notes/new.md", "acme/1.0/notes/changed.md",
                         "acme/readme.txt"}
    assert {r.origin for r in rows.values()} == {"folder"}
    assert (rows["acme/1.0/notes/indexed.md"].status, rows["acme/1.0/notes/indexed.md"].chunk_count) == ("indexed", 5)
    assert rows["acme/1.0/notes/new.md"].status == "pending"
    assert rows["acme/1.0/notes/changed.md"].status == "pending"  # the next run indexes the new content
    readme = rows["acme/readme.txt"]
    assert (readme.product, readme.version, readme.doc_type, readme.status) == ("acme", None, None, "pending")
    assert readme.content_type == "text/plain" and readme.filename == "readme.txt"
    # nothing was copied or moved
    files = sorted(p.relative_to(library.storage.documents_dir).as_posix()
                   for p in library.storage.documents_dir.rglob("*") if p.is_file())
    assert files == [".uploads/half-written.md", "acme/1.0/notes/changed.md", "acme/1.0/notes/indexed.md",
                     "acme/1.0/notes/new.md", "acme/1.0/notes/picture.png", "acme/readme.txt"]


def test_adopt_is_repeatable_and_skips_tracked_files(library, fake_repository):
    uploaded = upload(library)
    put(library, "acme/1.0/notes/by-hand.md")
    assert library.tracker.adopt_untracked() == 1
    assert library.tracker.adopt_untracked() == 0
    assert fake_repository.rows[uploaded.id].origin == "upload"  # never taken over
    assert len(fake_repository.rows) == 2


def test_adopt_skips_a_file_that_is_being_uploaded(library, fake_repository, monkeypatch):
    put(library, "acme/1.0/notes/notes.md")
    monkeypatch.setattr(library, "is_uploading", lambda source: source == "acme/1.0/notes/notes.md")
    library.tracker.is_uploading = library.is_uploading
    assert library.tracker.adopt_untracked() == 0
    assert fake_repository.rows == {}


# A successful run

def test_run_indexes_the_claimed_documents(library, fake_repository, fake_pipeline):
    document = upload(library)
    report = run(library, fake_pipeline)

    assert report.added == [document.source]
    row = fake_repository.rows[document.id]
    assert (row.status, row.chunk_count, row.error) == ("indexed", 2, None)
    assert row.indexed_at is not None


def test_run_reports_no_text_and_missing_files_as_failed(library, fake_repository, fake_pipeline):
    scanned = upload(library, name="scan.md")
    gone = upload(library, name="gone.md")
    (library.storage.documents_dir / gone.source).unlink()

    run(library, fake_pipeline)

    assert (fake_repository.rows[scanned.id].status, fake_repository.rows[scanned.id].error) == ("failed", NO_TEXT)
    assert (fake_repository.rows[gone.id].status, fake_repository.rows[gone.id].error) == ("failed", FILE_MISSING)


def test_failed_documents_are_retried_by_the_next_run(library, fake_repository, fake_pipeline):
    document = upload(library)
    fake_pipeline.error = ConnectionError("Ollama is down")
    with pytest.raises(ConnectionError):
        run(library, fake_pipeline)
    assert (fake_repository.rows[document.id].status, fake_repository.rows[document.id].error) == ("failed", OUTAGE)

    fake_pipeline.error = None
    run(library, fake_pipeline)
    assert (fake_repository.rows[document.id].status, fake_repository.rows[document.id].error) == ("indexed", None)


def test_documents_indexed_earlier_are_left_alone(library, fake_repository, fake_pipeline):
    first = upload(library, name="first.md")
    run(library, fake_pipeline)
    indexed = fake_repository.rows[first.id]

    upload(library, name="second.md")
    run(library, fake_pipeline)  # first.md is "unchanged" in this run

    assert fake_repository.rows[first.id] == indexed


def test_an_upload_during_a_run_stays_pending_for_the_next_one(library, fake_repository, fake_pipeline):
    first = upload(library, name="first.md")
    late = []
    fake_pipeline.during = lambda: late.append(upload(library, name="late.md"))

    run(library, fake_pipeline)

    # The run even indexed late.md's file (it was on disk in time), but late.md wasn't claimed by it.
    assert fake_repository.rows[first.id].status == "indexed"
    assert fake_repository.rows[late[0].id].status == "pending"
    assert fake_repository.rows[late[0].id].origin == "upload"  # not adopted as a folder file

    fake_pipeline.during = None
    run(library, fake_pipeline)
    assert fake_repository.rows[late[0].id].status == "indexed"


def test_a_changed_file_refreshes_its_indexed_document(library, fake_repository, fake_pipeline):
    document = upload(library, text="version one")
    run(library, fake_pipeline)
    new_hash = put(library, document.source, "version two, a little longer")

    report = run(library, fake_pipeline)

    assert report.updated == [document.source]
    row = fake_repository.rows[document.id]
    assert (row.status, row.file_hash, row.size_bytes) == ("indexed", new_hash, 28)


def test_a_vanished_file_marks_its_indexed_document_failed(library, fake_repository, fake_pipeline):
    document = upload(library)
    run(library, fake_pipeline)
    (library.storage.documents_dir / document.source).unlink()

    report = run(library, fake_pipeline)

    assert report.removed == [document.source]
    assert (fake_repository.rows[document.id].status, fake_repository.rows[document.id].error) == (
        "failed", FILE_MISSING)


def test_files_put_in_the_folder_during_operation_are_adopted_after_a_run(library, fake_repository,
                                                                          fake_pipeline):
    put(library, "acme/1.0/notes/by-hand.md", "by hand")
    put(library, "acme/1.0/notes/scan-by-hand.md", "no text")

    run(library, fake_pipeline)

    rows = {r.source: r for r in fake_repository.rows.values()}
    assert (rows["acme/1.0/notes/by-hand.md"].status, rows["acme/1.0/notes/by-hand.md"].chunk_count) == ("indexed", 2)
    assert (rows["acme/1.0/notes/scan-by-hand.md"].status, rows["acme/1.0/notes/scan-by-hand.md"].error) == (
        "failed", NO_TEXT)
    assert {r.origin for r in rows.values()} == {"folder"}


# A failed run

def test_a_failed_run_fails_only_what_it_claimed(library, fake_repository, fake_pipeline):
    indexed = upload(library, name="indexed.md")
    run(library, fake_pipeline)
    waiting = upload(library, name="waiting.md")
    late = []
    fake_pipeline.during = lambda: late.append(upload(library, name="late.md"))
    fake_pipeline.error = psycopg.OperationalError("connection lost")

    with pytest.raises(psycopg.OperationalError):
        run(library, fake_pipeline)

    assert fake_repository.rows[indexed.id].status == "indexed"
    assert (fake_repository.rows[waiting.id].status, fake_repository.rows[waiting.id].error) == ("failed", OUTAGE)
    assert fake_repository.rows[late[0].id].status == "pending"
    assert not any(r.status == "processing" for r in fake_repository.rows.values())


def test_if_failures_cant_be_recorded_the_next_run_releases_the_claims(library, fake_repository, fake_pipeline,
                                                                       caplog):
    document = upload(library)
    fake_pipeline.error = ConnectionError("Ollama is down")
    fake_repository.fail_on["mark_failed"] = psycopg.OperationalError("database down too")

    with pytest.raises(ConnectionError):  # the run's own error, not the bookkeeping one
        run(library, fake_pipeline)
    assert fake_repository.rows[document.id].status == "processing"
    assert "next run releases them" in caplog.text

    fake_pipeline.error = None
    del fake_repository.fail_on["mark_failed"]
    run(library, fake_pipeline)
    assert fake_repository.rows[document.id].status == "indexed"


def test_if_statuses_cant_be_recorded_after_a_run_the_claims_go_back_to_pending(library, fake_repository,
                                                                                 fake_pipeline):
    document = upload(library)
    fake_repository.fail_on["mark_indexed"] = psycopg.OperationalError("database went away")

    with pytest.raises(psycopg.OperationalError):
        run(library, fake_pipeline)
    assert fake_repository.rows[document.id].status == "pending"

    del fake_repository.fail_on["mark_indexed"]
    report = run(library, fake_pipeline)
    assert report.unchanged == [document.source]  # indexed by the first run already
    assert fake_repository.rows[document.id].status == "indexed"


def test_a_run_after_a_restart_releases_documents_left_processing(library, fake_repository, fake_pipeline):
    document = upload(library)
    fake_repository.claim_for_indexing()  # as if the server stopped mid-run

    run(library, fake_pipeline)

    assert fake_repository.rows[document.id].status == "indexed"


def test_an_upload_given_a_second_hard_link_is_marked_failed_not_indexed(library, fake_repository, fake_index,
                                                                         fake_pipeline, tmp_path_factory, caplog):
    document = upload(library)
    other_name = tmp_path_factory.mktemp("outside") / "other-name.md"
    os.link(library.storage.documents_dir / document.source, other_name)  # the same file, also outside

    run(library, fake_pipeline)

    row = fake_repository.rows[document.id]
    assert (row.status, row.error) == ("failed", FILE_MISSING)
    assert document.source not in fake_index.chunks
    assert "2 hard links" in caplog.text

    other_name.unlink()  # one name again: the next run (which retries failed documents) indexes it
    run(library, fake_pipeline)
    assert fake_repository.rows[document.id].status == "indexed"


def test_an_indexed_document_given_a_second_hard_link_leaves_the_index(library, fake_repository, fake_index,
                                                                       fake_pipeline, tmp_path_factory):
    document = upload(library)
    run(library, fake_pipeline)
    assert fake_repository.rows[document.id].status == "indexed"
    os.link(library.storage.documents_dir / document.source, tmp_path_factory.mktemp("outside") / "other.md")

    run(library, fake_pipeline)

    assert document.source not in fake_index.chunks  # its passages are removed
    row = fake_repository.rows[document.id]
    assert (row.status, row.error) == ("failed", FILE_MISSING)


def test_a_run_changes_nothing_when_the_documents_folder_cant_be_listed(library, fake_repository, fake_index,
                                                                        fake_pipeline, monkeypatch):
    indexed = upload(library, "indexed.md")
    run(library, fake_pipeline)
    waiting = upload(library, "waiting.md")
    chunks_before = dict(fake_index.chunks)
    root, real_iterdir = library.storage.documents_dir, Path.iterdir

    def iterdir(self):
        if self == root:
            raise PermissionError(13, "Access is denied", str(self))
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    with pytest.raises(DocumentsFolderUnreadable):
        run(library, fake_pipeline)

    assert fake_index.chunks == chunks_before  # nothing was removed from the index
    assert fake_repository.rows[indexed.id].status == "indexed"
    assert (fake_repository.rows[waiting.id].status, fake_repository.rows[waiting.id].error) == ("failed", OUTAGE)


def test_adoption_skips_files_the_library_could_not_serve_safely(library, fake_repository, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.md").write_text("secret")
    put(library, "acme/1.0/notes/ok.md")
    (library.storage.documents_dir / "acme" / "1.0" / "notes" / "linked.md").hardlink_to(outside / "secret.md")
    try:
        import _winapi
        _winapi.CreateJunction(str(outside), str(library.storage.documents_dir / "acme" / "elsewhere"))
    except (ImportError, OSError):
        pass  # the hard link case still runs

    assert library.tracker.adopt_untracked() == 1
    assert [r.source for r in fake_repository.rows.values()] == ["acme/1.0/notes/ok.md"]


# Building the library at server start

def test_build_library_releases_interrupted_claims_then_adopts(tmp_path, fake_repository, fake_index, monkeypatch):
    from rag.config import get_settings
    from rag.dms import library as library_module

    interrupted = upload(DocumentLibrary(DocumentStorage(tmp_path, max_bytes=MB), fake_repository,
                                         fake_index.access()))
    fake_repository.claim_for_indexing()  # as if the previous server stopped mid-run
    put(DocumentLibrary(DocumentStorage(tmp_path, max_bytes=MB), fake_repository, fake_index.access()),
        "acme/1.0/notes/by-hand.md")
    fake_repository.create_table = lambda: None
    tables = []
    monkeypatch.setattr(library_module, "DocumentRepository",
                        lambda url, table: tables.append(table) or fake_repository)
    monkeypatch.setattr(library_module, "index_access", lambda settings: fake_index.access())

    built = library_module.build_library(get_settings({"DATABASE_URL": "postgresql://u:p@h/db",
                                                       "DOCUMENTS_DIR": str(tmp_path)}), table="it_docs")

    assert tables == ["it_docs"]
    assert fake_repository.rows[interrupted.id].status == "pending"
    assert fake_repository.status_of("acme/1.0/notes/by-hand.md") == "pending"
    assert built.repository is fake_repository


def test_build_library_removes_uploads_abandoned_by_a_stopped_server(tmp_path, fake_repository, fake_index,
                                                                     monkeypatch):
    from rag.config import get_settings
    from rag.dms import library as library_module

    (tmp_path / ".uploads").mkdir()
    left = tmp_path / ".uploads" / ("0" * 32 + ".md")
    left.write_text("partial upload")
    then = time.time() - 2 * 60 * 60
    os.utime(left, (then, then))
    fake_repository.create_table = lambda: None
    monkeypatch.setattr(library_module, "DocumentRepository", lambda url, table: fake_repository)
    monkeypatch.setattr(library_module, "index_access", lambda settings: fake_index.access())

    library_module.build_library(get_settings({"DATABASE_URL": "postgresql://u:p@h/db",
                                               "DOCUMENTS_DIR": str(tmp_path)}))

    assert not left.exists()
    assert fake_repository.list_all() == []  # the temporary file was never a document
