"""What the document library needs from the RAG index (the chunks table), as plain functions.

The library never queries the chunks table itself: it goes through these, which are rag.database helpers in
the application and fakes in tests.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from rag import database
from rag.config import Settings
from rag.ingestion.pipeline import index_fingerprint


@dataclass(frozen=True)
class IndexAccess:
    delete_chunks: Callable[[str], int]  # remove one source file's chunks; returns how many
    count_chunks: Callable[[list[str]], dict[str, int]]  # chunks per source file (none: left out)
    indexed_files: Callable[[], dict[str, tuple[str, str]]]  # each indexed source -> (file_hash, embedding_model)
    fingerprint: str  # what ingestion now stores as embedding_model (index_fingerprint()); others count as not indexed


def index_access(settings: Settings) -> IndexAccess:
    url, table = settings.database_url, settings.vector_table
    return IndexAccess(
        delete_chunks=partial(database.delete_chunks, url, table),
        count_chunks=partial(database.chunk_counts, url, table),
        indexed_files=partial(database.indexed_files, url, table),
        fingerprint=index_fingerprint(settings),
    )
