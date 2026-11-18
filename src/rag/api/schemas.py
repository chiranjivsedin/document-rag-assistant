"""Request and response bodies of the HTTP API; FastAPI also publishes them in its OpenAPI schema."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from rag.retrieval.search import DEFAULT_K

# Optional filter: surrounding spaces trimmed; an empty value means "no filter", as in retrieve().
Filter = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]


class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    llm_model: str
    embedding_model: str
    problems: list[str]  # one plain-English line per problem; empty when status is "ok"


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelt filter is an error, not silently ignored

    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    product: Filter | None = None
    version: Filter | None = None
    k: int = Field(default=DEFAULT_K, ge=1, le=20, description="Number of passages to retrieve")


class ChatSource(BaseModel):
    number: int = Field(description="Matches the [n] citations in the answer")
    source: str | None
    product: str | None
    version: str | None
    doc_type: str | None
    page: int | None
    distance: float = Field(description="Cosine distance to the question: 0 = same meaning, larger = less related")
    excerpt: str = Field(description="The whole passage given to the model, so the answer can be checked against it")


class ChatResponse(BaseModel):
    answer: str
    no_answer: bool = Field(description="True when the documents don't contain the answer")
    sources: list[ChatSource]  # empty when no_answer is true
    model: str
    elapsed_ms: int


class IngestReportBody(BaseModel):
    """What one ingestion run did; each list holds paths relative to the documents folder."""

    added: list[str]
    updated: list[str]
    unchanged: list[str]
    removed: list[str]
    no_text: list[str] = Field(description="Files with no extractable text (e.g. scanned PDFs), skipped")
    chunks_stored: int


class IngestJobResponse(BaseModel):
    job_id: str
    status: Literal["running", "succeeded", "failed"]
    started_at: datetime
    finished_at: datetime | None
    report: IngestReportBody | None = Field(description="Set when status is succeeded")
    error: str | None = Field(description="Set when status is failed")


class IngestConflictResponse(BaseModel):
    detail: str
    job_id: str = Field(description="The job that is already running")


class DocumentResponse(BaseModel):
    """One document in the library: its original file and whether it is in the index yet."""

    id: uuid.UUID
    source: str = Field(description="Path relative to the documents folder, as in a chat source")
    filename: str = Field(description="The name to show: the uploaded file's own name")
    product: str | None
    version: str | None
    doc_type: str | None
    content_type: str
    size_bytes: int
    file_hash: str = Field(description="SHA-256 of the content")
    origin: Literal["upload", "folder"] = Field(description="upload: added here; folder: found in the documents folder")
    status: Literal["pending", "processing", "indexed", "failed"]
    error: str | None = Field(description="Set when status is failed")
    chunk_count: int | None
    added_at: datetime
    indexed_at: datetime | None


class UploadedDocumentResponse(DocumentResponse):
    ingest_job_id: str | None = Field(
        description="The ingestion job this upload started (follow it at /documents/ingest/{job_id}). Null when a "
                    "job was already running (it indexes this document next) or Ollama or the database is down "
                    "(the document stays pending until indexing is started)"
    )


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]  # most recently added first


class DeletionFailedResponse(BaseModel):
    detail: str
    file_deleted: bool = Field(description="The original file is gone (it may already have been missing)")
    chunks_deleted: bool = Field(description="Its passages are out of the index")
    record_deleted: bool = Field(description="It is no longer listed")


# Master data for tags (rag.dms.tags, endpoints in rag.api.tags).

class TagValueBody(BaseModel):
    id: int
    code: str = Field(description="Stable: what documents store (for product, version and doc_type, the folder name)")
    label: str = Field(description="The name to show")
    active: bool = Field(description="False = retired: no new use, still shown and still filtering")


class TagCategoryBody(BaseModel):
    key: str = Field(description="product, version, doc_type, or an extra category's key")
    label: str
    builtin: bool = Field(description="product, version and doc_type: the document's folders")
    multiple: bool = Field(description="Whether a document may have several values")
    values: list[TagValueBody]  # active and retired, in sort order


class TagListResponse(BaseModel):
    categories: list[TagCategoryBody]  # built-ins first


class NewTagCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(max_length=100, description="Lowercase letters, digits and hyphens, starting with a letter")
    label: str = Field(max_length=200)
    multiple: bool = False


class NewTagValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(max_length=100, description="Letters, digits, dots, hyphens, underscores; never renamed")
    label: str = Field(max_length=200)


class TagValueChange(BaseModel):
    """Only the fields given change; a code never changes."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=200)
    active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=1_000_000)
