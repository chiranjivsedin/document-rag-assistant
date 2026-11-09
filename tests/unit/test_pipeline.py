import hashlib

import pytest

from rag.config import get_settings
from rag.ingestion.pipeline import CHUNKING_VERSION, index_fingerprint, ingest

SETTINGS = get_settings({"CHUNK_SIZE": "200", "CHUNK_OVERLAP": "20"})
MODEL = SETTINGS.embedding_model
FINGERPRINT = index_fingerprint(SETTINGS)


class FakeStore:
    """Records what the pipeline asks the vector store to do."""

    def __init__(self):
        self.added = []
        self.deleted = []

    def add_documents(self, docs):
        self.added.extend(docs)

    def delete(self, filter):
        self.deleted.append(filter["source"])


def write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def store():
    return FakeStore()


def test_new_files_are_chunked_and_stored(tmp_path, store):
    write(tmp_path, "acme/1.0/release-notes/notes.md", "Feature X is deprecated. " * 20)
    report = ingest(tmp_path, store, indexed={}, settings=SETTINGS)
    assert report.added == ["acme/1.0/release-notes/notes.md"]
    assert report.chunks_stored == len(store.added) > 1
    assert all(c.metadata["embedding_model"] == FINGERPRINT and c.metadata["version"] == "1.0" for c in store.added)
    assert store.deleted == []


def test_unchanged_files_are_skipped(tmp_path, store):
    digest = write(tmp_path, "acme/1.0/release-notes/notes.md", "Same text.")
    indexed = {"acme/1.0/release-notes/notes.md": (digest, FINGERPRINT)}
    report = ingest(tmp_path, store, indexed, SETTINGS)
    assert report.unchanged == ["acme/1.0/release-notes/notes.md"]
    assert store.added == [] and store.deleted == []


def test_changed_files_replace_their_old_chunks(tmp_path, store):
    write(tmp_path, "acme/1.0/release-notes/notes.md", "New text.")
    indexed = {"acme/1.0/release-notes/notes.md": ("old-hash", FINGERPRINT)}
    report = ingest(tmp_path, store, indexed, SETTINGS)
    assert report.updated == ["acme/1.0/release-notes/notes.md"]
    assert store.deleted == ["acme/1.0/release-notes/notes.md"]
    assert [c.page_content for c in store.added] == ["New text."]


def test_the_fingerprint_names_the_model_and_the_chunking():
    assert FINGERPRINT == f"{MODEL};chunks=v{CHUNKING_VERSION}/200/20"


@pytest.mark.parametrize("stored", [
    "older-model;chunks=v2/200/20",  # another embedding model
    "nomic-embed-text",  # chunks from before the fingerprint (chunking version 1): re-chunked once
    f"{MODEL};chunks=v{CHUNKING_VERSION - 1}/200/20",  # older chunking code
    f"{MODEL};chunks=v{CHUNKING_VERSION}/1000/150",  # other CHUNK_SIZE / CHUNK_OVERLAP
])
def test_a_model_or_chunking_change_reprocesses_the_file(tmp_path, store, stored):
    digest = write(tmp_path, "notes.md", "Same text.")
    report = ingest(tmp_path, store, {"notes.md": (digest, stored)}, SETTINGS)
    assert report.updated == ["notes.md"]
    assert store.deleted == ["notes.md"]
    assert [c.metadata["embedding_model"] for c in store.added] == [FINGERPRINT]


def test_files_gone_from_disk_are_removed(tmp_path, store):
    report = ingest(tmp_path, store, {"acme/1.0/old.md": ("h", FINGERPRINT)}, SETTINGS)
    assert report.removed == ["acme/1.0/old.md"]
    assert store.deleted == ["acme/1.0/old.md"]


def test_files_without_text_are_reported_not_stored(tmp_path, store):
    write(tmp_path, "blank.txt", "   ")
    report = ingest(tmp_path, store, {}, SETTINGS)
    assert report.no_text == ["blank.txt"]
    assert store.added == []
