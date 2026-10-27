"""Ingestion pipeline: load -> chunk -> embed -> store, skipping files that haven't changed."""

from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.vectorstores import VectorStore

from rag.config import Settings
from rag.ingestion.chunking import split_documents
from rag.ingestion.loaders import file_metadata, iter_files, load_file


@dataclass
class IngestReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    no_text: list[str] = field(default_factory=list)
    chunks_stored: int = 0


def ingest(
    documents_dir: Path,
    store: VectorStore,
    indexed: dict[str, tuple[str, str]],
    settings: Settings,
) -> IngestReport:
    """Bring the store in line with documents_dir.

    `indexed` maps each already-stored source to its (file_hash, embedding_model). A file is
    re-processed when its content or the embedding model changed; files no longer on disk are removed.
    """
    report = IngestReport()
    on_disk = set()

    for path in iter_files(documents_dir):
        meta = file_metadata(path, documents_dir)
        source = meta["source"]
        on_disk.add(source)
        previous = indexed.get(source)
        if previous == (meta["file_hash"], settings.embedding_model):
            report.unchanged.append(source)
            continue

        chunks = split_documents(load_file(path, documents_dir), settings.chunk_size, settings.chunk_overlap)
        for chunk in chunks:
            chunk.metadata["embedding_model"] = settings.embedding_model

        if previous:
            store.delete(filter={"source": source})
        if not chunks:
            report.no_text.append(source)
            continue
        store.add_documents(chunks)
        report.chunks_stored += len(chunks)
        (report.updated if previous else report.added).append(source)

    for source in sorted(indexed.keys() - on_disk):
        store.delete(filter={"source": source})
        report.removed.append(source)

    return report
