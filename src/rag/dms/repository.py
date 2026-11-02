"""The documents table: one row per original file in DOCUMENTS_DIR, with its indexing status.

Kept apart from the RAG code. A row is linked to its chunks only through `source` (the file's path
relative to DOCUMENTS_DIR, the same value as chunks.source), never by a foreign key, and nothing here
reads or writes the chunks table. Like that table, it is created by the application (`create_table()`);
there are no migrations.

Status moves pending -> processing -> indexed | failed. An indexing run first claims the rows waiting for
it (pending, or failed by an earlier run, so they are retried); they become "processing", and only claimed
rows can be finished, so a run never marks a document it didn't claim, such as one indexed earlier or one
uploaded while it was running.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from psycopg import sql
from psycopg.rows import dict_row

from rag import database

Status = Literal["pending", "processing", "indexed", "failed"]
# "upload": stored through the API. "folder": a file already in DOCUMENTS_DIR, adopted where it is (not copied).
Origin = Literal["upload", "folder"]

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS {table} (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source       text NOT NULL UNIQUE,
    filename     text NOT NULL,
    product      text,
    version      text,
    doc_type     text,
    content_type text NOT NULL,
    size_bytes   bigint NOT NULL CHECK (size_bytes >= 0),
    file_hash    text NOT NULL CHECK (file_hash ~ '^[0-9a-f]{{64}}$'),
    origin       text NOT NULL CHECK (origin IN ('upload', 'folder')),
    status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'indexed', 'failed')),
    error        text,
    chunk_count  integer CHECK (chunk_count >= 0),
    added_at     timestamptz NOT NULL DEFAULT now(),
    indexed_at   timestamptz,
    CHECK (origin = 'folder' OR (product IS NOT NULL AND version IS NOT NULL AND doc_type IS NOT NULL)),
    CHECK ((status = 'failed') = (error IS NOT NULL)),
    CHECK (status <> 'indexed' OR indexed_at IS NOT NULL)
)
"""

_COLUMNS = ("source", "filename", "product", "version", "doc_type", "content_type", "size_bytes", "file_hash")


@dataclass(frozen=True)
class NewDocument:
    """A file to start tracking. product/version/doc_type are its first three folders, exactly as the
    loaders derive them for its chunks; an adopted file placed higher up the tree has None for the rest."""

    source: str  # path relative to DOCUMENTS_DIR, with forward slashes
    filename: str  # the name to show: the original upload name, or the file's own name when adopted
    content_type: str
    size_bytes: int
    file_hash: str  # SHA-256 of the content, lowercase hex (as in chunks.file_hash)
    product: str | None = None
    version: str | None = None
    doc_type: str | None = None

    def __post_init__(self) -> None:
        parts = self.source.split("/")
        if (not self.source or "\\" in self.source or ":" in self.source
                or any(part in ("", ".", "..") for part in parts)):
            raise ValueError(f"source must be a relative path with forward slashes, got {self.source!r}")
        folders = parts[:-1]
        for level, value in enumerate((self.product, self.version, self.doc_type)):
            expected = folders[level] if level < len(folders) else None
            if value != expected:
                raise ValueError(f"product/version/doc_type must match the folders of {self.source!r}")
        if not self.filename or "/" in self.filename or "\\" in self.filename:
            raise ValueError(f"filename must be a plain file name, got {self.filename!r}")
        if not self.content_type:
            raise ValueError("content_type is required")
        if self.size_bytes < 0:
            raise ValueError("size_bytes can't be negative")
        if len(self.file_hash) != 64 or any(c not in "0123456789abcdef" for c in self.file_hash):
            raise ValueError("file_hash must be a lowercase hex SHA-256")


@dataclass(frozen=True)
class DocumentRecord:
    id: uuid.UUID
    source: str
    filename: str
    product: str | None
    version: str | None
    doc_type: str | None
    content_type: str
    size_bytes: int
    file_hash: str
    origin: Origin
    status: Status
    error: str | None  # a client-safe reason; set only when status is "failed"
    chunk_count: int | None
    added_at: datetime
    indexed_at: datetime | None


class DuplicateDocument(Exception):
    def __init__(self, source: str):
        super().__init__(f"A document is already stored at {source}")
        self.source = source


class DocumentRepository:
    """Reads and writes the documents table; each call uses its own short connection, like rag.database."""

    def __init__(self, database_url: str, table: str = "documents"):
        if not table.isidentifier():
            raise ValueError(f"table must be a plain table name, got {table!r}")
        self._database_url = database_url
        self._table = sql.Identifier(table)

    def create_table(self) -> None:
        """Create the table if it doesn't exist yet; safe to call every time the application starts."""
        self._execute(_CREATE_TABLE)

    def add(self, new: NewDocument) -> DocumentRecord:
        """Track an uploaded file as pending. Raises DuplicateDocument if its source is already tracked."""
        if not (new.product and new.version and new.doc_type):
            raise ValueError("An uploaded document needs a product, version and doc_type")
        row = self._insert(new, origin="upload", status="pending", chunk_count=None)
        if row is None:
            raise DuplicateDocument(new.source)
        return row

    def adopt(self, new: NewDocument, chunk_count: int | None = None, error: str | None = None
              ) -> DocumentRecord | None:
        """Track a file that is already in DOCUMENTS_DIR, where it is (it is not copied).

        Recorded as indexed when `chunk_count` is given, as failed when `error` is given, otherwise as
        pending. Returns None, changing nothing, if its source is already tracked.
        """
        if error:
            return self._insert(new, origin="folder", status="failed", chunk_count=None, error=error)
        status = "pending" if chunk_count is None else "indexed"
        return self._insert(new, origin="folder", status=status, chunk_count=chunk_count)

    def get(self, document_id: uuid.UUID) -> DocumentRecord | None:
        rows = self._fetch("SELECT * FROM {table} WHERE id = %s", (document_id,))
        return rows[0] if rows else None

    def get_by_source(self, source: str) -> DocumentRecord | None:
        rows = self._fetch("SELECT * FROM {table} WHERE source = %s", (source,))
        return rows[0] if rows else None

    def list_all(self) -> list[DocumentRecord]:
        """Every tracked document, most recently added first."""
        return self._fetch("SELECT * FROM {table} ORDER BY added_at DESC, source")

    def delete(self, document_id: uuid.UUID) -> bool:
        """Remove the row only (not the file or its chunks); False if there was no such row."""
        return self._execute("DELETE FROM {table} WHERE id = %s", (document_id,)) == 1

    def claim_for_indexing(self) -> list[DocumentRecord]:
        """Start an indexing run: every pending or failed document becomes processing (failed ones are
        retried). Returns the claimed rows."""
        return self._fetch(
            "UPDATE {table} SET status = 'processing', error = NULL "
            "WHERE status IN ('pending', 'failed') RETURNING *"
        )

    def mark_indexed(self, document_id: uuid.UUID, chunk_count: int) -> bool:
        """Finish a claimed document as indexed. False (nothing changed) if it wasn't processing."""
        return self._execute(
            "UPDATE {table} SET status = 'indexed', error = NULL, chunk_count = %s, indexed_at = now() "
            "WHERE id = %s AND status = 'processing'",
            (chunk_count, document_id),
        ) == 1

    def mark_failed(self, document_ids: Iterable[uuid.UUID], error: str) -> int:
        """Finish claimed documents as failed with a client-safe reason. Documents that aren't processing
        (indexed earlier, still pending, or unknown) are left alone. Returns how many changed."""
        if not error:
            raise ValueError("A failed document needs a reason")
        ids = list(document_ids)
        if not ids:
            return 0
        return self._execute(
            "UPDATE {table} SET status = 'failed', error = %s WHERE id = ANY(%s) AND status = 'processing'",
            (error, ids),
        )

    def release_processing(self) -> int:
        """Put documents left processing by an interrupted run (e.g. a server restart) back to pending."""
        return self._execute("UPDATE {table} SET status = 'pending' WHERE status = 'processing'")

    def count_pending(self) -> int:
        with database.connect(self._database_url) as conn:
            return conn.execute(self._query("SELECT count(*) FROM {table} WHERE status = 'pending'")).fetchone()[0]

    def refresh_indexed(self, source: str, file_hash: str, size_bytes: int, chunk_count: int) -> bool:
        """Record that an indexed document's file changed on disk and was indexed again. Only touches a
        document that is indexed; False if there was none."""
        return self._execute(
            "UPDATE {table} SET file_hash = %s, size_bytes = %s, chunk_count = %s, indexed_at = now() "
            "WHERE source = %s AND status = 'indexed'",
            (file_hash, size_bytes, chunk_count, source),
        ) == 1

    def mark_missing(self, sources: Iterable[str], error: str) -> int:
        """Mark indexed documents whose file has disappeared from DOCUMENTS_DIR as failed. Returns how many."""
        sources = list(sources)
        if not sources:
            return 0
        return self._execute(
            "UPDATE {table} SET status = 'failed', error = %s WHERE source = ANY(%s) AND status = 'indexed'",
            (error, sources),
        )

    def _insert(self, new: NewDocument, origin: Origin, status: Status, chunk_count: int | None,
                error: str | None = None) -> DocumentRecord | None:
        values = [getattr(new, column) for column in _COLUMNS]
        indexed_at = "now()" if status == "indexed" else "NULL"
        rows = self._fetch(
            f"INSERT INTO {{table}} ({', '.join(_COLUMNS)}, origin, status, chunk_count, error, indexed_at) "
            f"VALUES ({', '.join(['%s'] * len(_COLUMNS))}, %s, %s, %s, %s, {indexed_at}) "
            "ON CONFLICT (source) DO NOTHING RETURNING *",
            (*values, origin, status, chunk_count, error),
        )
        return rows[0] if rows else None

    def _query(self, template: str) -> sql.Composed:
        return sql.SQL(template).format(table=self._table)

    def _fetch(self, template: str, params: tuple = ()) -> list[DocumentRecord]:
        with database.connect(self._database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(self._query(template), params)
            return [_record(row) for row in cur.fetchall()]

    def _execute(self, template: str, params: tuple = ()) -> int:
        """Run a statement; returns the number of rows it changed."""
        with database.connect(self._database_url) as conn:
            return conn.execute(self._query(template), params).rowcount


def _record(row: dict) -> DocumentRecord:
    return DocumentRecord(**row)
