"""The document library: uploads, lookups and deletions across the files, the documents table and the index.

The shared glue behind the /documents endpoints, kept out of the endpoints themselves (as rag.service is for
the RAG side). It never indexes anything itself: an upload is recorded as pending, and an ingestion run, wrapped
by its IndexingTracker (rag.dms.tracking), indexes it and records the outcome.
"""

import logging
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from rag.config import Settings
from rag.dms.index import IndexAccess, index_access
from rag.dms.repository import DocumentRecord, DocumentRepository, DuplicateDocument, NewDocument
from rag.dms.storage import CONTENT_TYPES, DocumentStorage, check_folder_name, clean_filename
from rag.dms.tracking import IndexingTracker

logger = logging.getLogger(__name__)


class DocumentNotFound(Exception):
    pass


class DocumentExists(Exception):
    def __init__(self, source: str):
        super().__init__(f"A document already exists at {source}.")
        self.source = source


class FileMissing(Exception):
    """The document is recorded, but its original file is no longer on disk."""


class StorageFailed(Exception):
    """The file system refused to store an upload (e.g. the disk is full); the message is safe to show."""


@dataclass(frozen=True)
class DeletionSteps:
    file_deleted: bool = False  # also true when the file was already gone
    chunks_deleted: bool = False
    record_deleted: bool = False


class DeletionFailed(Exception):
    """A deletion stopped part-way. `steps` says what was done; `__cause__` is the error that stopped it.

    Each step is safe to repeat, so deleting the document again finishes the job.
    """

    def __init__(self, document: DocumentRecord, steps: DeletionSteps):
        super().__init__(f"Deleting {document.source} stopped part-way: {steps}")
        self.document = document
        self.steps = steps


class DocumentLibrary:
    def __init__(self, storage: DocumentStorage, repository: DocumentRepository, index: IndexAccess):
        self.storage = storage
        self.repository = repository
        self.index = index
        self.tracker = IndexingTracker(repository, storage, index, self.is_uploading)
        # Paths between "file saved" and "record added" by an upload, so a concurrent indexing run never
        # adopts the file as found in the folder (which would make the upload fail as a duplicate).
        self._uploading: set[str] = set()
        self._uploading_lock = threading.Lock()

    def is_uploading(self, source: str) -> bool:
        with self._uploading_lock:
            return source in self._uploading

    def upload(self, stream: BinaryIO, filename: str, product: str, version: str, doc_type: str) -> DocumentRecord:
        """Store an uploaded file and record it as pending.

        Raises InvalidDocument (including DocumentTooLarge) or DocumentExists, leaving nothing behind.
        """
        product = check_folder_name(product, "product")
        version = check_folder_name(version, "version")
        doc_type = check_folder_name(doc_type, "doc_type")
        stored_name = clean_filename(filename)
        source = self.storage.source_for(product, version, doc_type, stored_name)
        with self._uploading_lock:
            if source in self._uploading:
                raise DocumentExists(source)  # the same path is being uploaded right now
            self._uploading.add(source)
        try:
            return self._store(stream, filename, source)
        finally:
            with self._uploading_lock:
                self._uploading.discard(source)

    def _store(self, stream: BinaryIO, filename: str, source: str) -> DocumentRecord:
        if self.repository.get_by_source(source) is not None or self.storage.exists(source):
            raise DocumentExists(source)
        try:
            saved = self.storage.save(stream, source)
        except FileExistsError:
            raise DocumentExists(source) from None  # another upload got there first
        except OSError as error:
            logger.error("Couldn't store an upload at %s", source, exc_info=error)
            raise StorageFailed("The file couldn't be stored on the server.") from error

        product, version, doc_type, stored_name = source.split("/")  # with the capitals already on disk
        try:
            return self.repository.add(NewDocument(
                source=source, filename=_display_name(filename, stored_name),
                content_type=CONTENT_TYPES[Path(stored_name).suffix.lower()],
                size_bytes=saved.size_bytes, file_hash=saved.file_hash,
                product=product, version=version, doc_type=doc_type,
            ))
        except BaseException as error:
            # The file was written by this upload a moment ago; without its row nothing would list or delete it.
            self._remove_unrecorded_file(source)
            if isinstance(error, DuplicateDocument):
                raise DocumentExists(source) from None
            raise

    def list(self) -> list[DocumentRecord]:
        return self.repository.list_all()

    def get(self, document_id: uuid.UUID) -> DocumentRecord:
        document = self.repository.get(document_id)
        if document is None:
            raise DocumentNotFound(document_id)
        return document

    def original_file(self, document_id: uuid.UUID) -> tuple[DocumentRecord, Path]:
        """The document and the path of its original file; raises FileMissing if the file is gone, or
        UnsafePath if it can't be served safely (e.g. it has another hard link)."""
        document = self.get(document_id)
        path = self.storage.readable_file(document.source)
        if path is None:
            raise FileMissing(document.source)
        return document, path

    def delete(self, document_id: uuid.UUID) -> DocumentRecord:
        """Permanently delete a document: its file, then its passages in the index, then its record.

        This order keeps every failure safe to retry. If the file can't be deleted (for example it is open
        in another program), nothing has changed. If the passages can't be removed, the file is gone, so
        the next ingestion run removes them anyway. The record goes last, so the document stays listed
        (and can be deleted again) until everything else is gone. Raises DeletionFailed if a step fails.
        """
        document = self.get(document_id)
        steps = DeletionSteps()
        try:
            self.storage.delete(document.source)
            steps = DeletionSteps(file_deleted=True)
            self.index.delete_chunks(document.source)
            steps = DeletionSteps(file_deleted=True, chunks_deleted=True)
            self.repository.delete(document.id)
        except Exception as error:
            logger.error("Deleting document %s (%s) stopped part-way: %s", document.id, document.source, steps,
                         exc_info=error)
            raise DeletionFailed(document, steps) from error
        logger.info("Deleted document %s (%s)", document.id, document.source)
        return document

    def _remove_unrecorded_file(self, source: str) -> None:
        try:
            self.storage.delete(source)
        except Exception as cleanup_error:
            logger.error("Couldn't remove %s after its record failed; delete it by hand", source,
                         exc_info=cleanup_error)


def build_library(settings: Settings, table: str = "documents") -> DocumentLibrary:
    """Connect to the documents table (creating it on first use) and the documents folder.

    Documents left processing by a server that stopped mid-run go back to pending, and files already in the
    documents folder are adopted, so the library lists everything the assistant can answer from. This runs
    once per server process, before its first ingestion run, which is why it can't reset a running job: the
    API must run as a single process (one uvicorn worker), and an old process must be stopped completely
    before a new one starts.
    """
    if not settings.database_url:
        raise ValueError("DATABASE_URL is required for the document library")
    repository = DocumentRepository(settings.database_url, table=table)
    repository.create_table()
    library = DocumentLibrary(
        storage=DocumentStorage(Path(settings.documents_dir), max_bytes=settings.max_upload_mb * 1024 * 1024),
        repository=repository,
        index=index_access(settings),
    )
    repository.release_processing()
    if library.storage.documents_dir.is_dir():
        library.tracker.adopt_untracked()
    return library


def _display_name(original: str, stored_name: str) -> str:
    """The uploaded file's own name (without any folders the browser sent), or the stored name if it has none."""
    name = original.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name[:255] or stored_name
