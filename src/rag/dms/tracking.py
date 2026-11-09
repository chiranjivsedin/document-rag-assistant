"""Document statuses kept in step with ingestion runs, and files already in DOCUMENTS_DIR adopted.

An ingestion run indexes the whole documents folder with the existing pipeline, unchanged. Around it, the
tracker:

1. puts documents left "processing" by an interrupted run (e.g. a server restart) back to pending. Runs
   never overlap: the API runs one ingestion job at a time, in a single process;
2. claims the documents waiting for indexing (pending, or failed by an earlier run, which are retried);
3. runs the ingestion;
4. finishes only the claimed documents, from the run's report: indexed with their chunk count, or failed
   with a reason. A document uploaded during the run wasn't claimed, so it stays pending for the next run;
   a document indexed earlier changes only if the report shows its file changed or disappeared;
5. adopts files the run indexed that the library doesn't track yet (put in the folder by hand), where
   they are.

If the ingestion fails, every claimed document is marked failed with the run's error, so none is left
processing; the next run retries them.
"""

import logging
from collections.abc import Callable
from pathlib import Path

from rag.dms.index import IndexAccess
from rag.dms.repository import DocumentRecord, DocumentRepository, NewDocument
from rag.dms.storage import CONTENT_TYPES, DocumentStorage, UnsafePath
from rag.ingestion.loaders import file_metadata, iter_files
from rag.ingestion.pipeline import IngestReport

logger = logging.getLogger(__name__)

NO_TEXT = "No text could be extracted (for example a scanned PDF without a text layer)."
FILE_MISSING = "The original file is no longer in the documents folder."


class IndexingTracker:
    def __init__(self, repository: DocumentRepository, storage: DocumentStorage, index: IndexAccess,
                 is_uploading: Callable[[str], bool]):
        """`is_uploading(source)` is true while an upload to that path is between saving its file and
        recording it; such a file is never adopted, so the upload keeps its own record."""
        self.repository = repository
        self.storage = storage
        self.documents_dir = storage.documents_dir
        self.index = index
        self.is_uploading = is_uploading

    def adopt_untracked(self) -> int:
        """Record supported files in the documents folder that aren't tracked yet, where they are: indexed if
        the index already holds this exact file (same content, embedding model and chunking), otherwise pending
        (the next run indexes them). Returns how many were adopted."""
        tracked = {document.source for document in self.repository.list_all()}
        new_documents = [new for path in iter_files(self.documents_dir)
                         if (new := self._untracked(path, tracked)) is not None]
        if not new_documents:
            return 0
        indexed = self.index.indexed_files()
        current = [new.source for new in new_documents
                   if indexed.get(new.source) == (new.file_hash, self.index.fingerprint)]
        counts = self.index.count_chunks(current) if current else {}
        adopted = 0
        for new in new_documents:
            chunk_count = counts.get(new.source, 0) if new.source in current else None
            adopted += self.repository.adopt(new, chunk_count=chunk_count) is not None
        if adopted:
            logger.info("Adopted %d file(s) already in the documents folder", adopted)
        return adopted

    def run(self, ingest: Callable[[], IngestReport], failure_message: Callable[[Exception], str]) -> IngestReport:
        """Run `ingest` and record its outcome on the documents it covered. Raises what `ingest` raises."""
        released = self.repository.release_processing()
        if released:
            logger.warning("%d document(s) left processing by an interrupted run are pending again", released)
        claimed = self.repository.claim_for_indexing()
        try:
            report = ingest()
        except Exception as error:
            self._fail_claimed(claimed, failure_message(error))
            raise
        try:
            self._record(report, claimed)
        except Exception:
            # The index is up to date but the statuses couldn't all be written (e.g. the database went away).
            # Unfinished claims go back to pending; the next run finds those files unchanged and finishes them.
            self._release_claims()
            raise
        return report

    def _record(self, report: IngestReport, claimed: list[DocumentRecord]) -> None:
        indexed_now = set(report.added) | set(report.updated) | set(report.unchanged)
        no_text = set(report.no_text)
        counts = self.index.count_chunks(sorted(indexed_now)) if indexed_now else {}

        claimed_sources = set()
        for document in claimed:
            claimed_sources.add(document.source)
            if document.source in indexed_now:
                self.repository.mark_indexed(document.id, counts.get(document.source, 0))
            else:
                self.repository.mark_failed([document.id], NO_TEXT if document.source in no_text else FILE_MISSING)

        # Documents indexed before this run: only a changed or vanished file changes them.
        for source in sorted(set(report.updated) - claimed_sources):
            path = self.documents_dir / source
            try:
                file_hash, size = file_metadata(path, self.documents_dir)["file_hash"], path.stat().st_size
            except OSError:
                continue  # gone again since the run; the next run records that
            self.repository.refresh_indexed(source, file_hash, size, counts.get(source, 0))
        self.repository.mark_missing(sorted(set(report.removed) - claimed_sources), FILE_MISSING)

        self._adopt_from_report(indexed_now | no_text, no_text, counts)

    def _adopt_from_report(self, seen: set[str], no_text: set[str], counts: dict[str, int]) -> None:
        tracked = {document.source for document in self.repository.list_all()}
        for source in sorted(seen - tracked):
            new = self._untracked(self.documents_dir / source, tracked)
            if new is None:
                continue
            if source in no_text:
                self.repository.adopt(new, error=NO_TEXT)
            else:
                self.repository.adopt(new, chunk_count=counts.get(source, 0))

    def _untracked(self, path: Path, tracked: set[str]) -> NewDocument | None:
        """The record for a file the library doesn't track yet, or None: tracked, mid-upload, or a file the
        library couldn't serve or delete safely (an unsafe name, a link, more than one hard link)."""
        source = path.relative_to(self.documents_dir).as_posix()
        if source in tracked or self.is_uploading(source):
            return None
        try:
            if self.storage.readable_file(source) is None:
                return None  # gone since the folder was listed
            meta = file_metadata(path, self.documents_dir)
            return NewDocument(
                source=source, filename=path.name, content_type=CONTENT_TYPES[path.suffix.lower()],
                size_bytes=path.stat().st_size, file_hash=meta["file_hash"],
                product=meta["product"], version=meta["version"], doc_type=meta["doc_type"],
            )
        except (UnsafePath, OSError, ValueError, KeyError) as error:
            logger.warning("Not adopting %s: %s", source, error)
            return None

    def _fail_claimed(self, claimed: list[DocumentRecord], reason: str) -> None:
        try:
            self.repository.mark_failed([document.id for document in claimed], reason)
        except Exception as error:
            logger.error("Couldn't mark %d claimed document(s) as failed; the next run releases them",
                         len(claimed), exc_info=error)

    def _release_claims(self) -> None:
        try:
            self.repository.release_processing()
        except Exception as error:
            logger.error("Couldn't release claimed documents; the next run does", exc_info=error)
