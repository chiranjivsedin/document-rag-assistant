"""Ingestion pipeline: load -> chunk -> embed -> store, skipping files that haven't changed."""

from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.vectorstores import VectorStore

from rag.config import Settings
from rag.ingestion.chunking import split_documents
from rag.ingestion.loaders import file_metadata, iter_files, load_file

# Bump when a change to loading or chunking should rebuild every file's chunks: the next ingest run then
# re-processes every file once. Version 2: pages chunked as one text, section headings, no running headers.
CHUNKING_VERSION = 2


def index_fingerprint(settings: Settings) -> str:
    """What each chunk stores in its `embedding_model` column: the embedding model plus how the text was cut
    (CHUNKING_VERSION, CHUNK_SIZE, CHUNK_OVERLAP), e.g. "nomic-embed-text;chunks=v2/1000/150".

    A file whose chunks hold another value is re-processed, so changing the model, the chunk settings or the
    chunking code re-indexes everything on the next run. (The column keeps its name, so existing tables work.)
    """
    return f"{settings.embedding_model};chunks=v{CHUNKING_VERSION}/{settings.chunk_size}/{settings.chunk_overlap}"


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

    `indexed` maps each already-stored source to its (file_hash, embedding_model column). A file is
    re-processed when its content or its index_fingerprint() changed; files no longer on disk are removed.
    """
    report = IngestReport()
    on_disk = set()
    fingerprint = index_fingerprint(settings)

    for path in iter_files(documents_dir):
        meta = file_metadata(path, documents_dir)
        source = meta["source"]
        on_disk.add(source)
        previous = indexed.get(source)
        if previous == (meta["file_hash"], fingerprint):
            report.unchanged.append(source)
            continue

        chunks = split_documents(load_file(path, documents_dir), settings.chunk_size, settings.chunk_overlap)
        for chunk in chunks:
            chunk.metadata["embedding_model"] = fingerprint

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
