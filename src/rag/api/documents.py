"""The document library's endpoints: upload, list, read, open and delete original files.

An upload is recorded as pending and then indexed by an ingestion job: the upload starts one when none is
running, and a running job starts another for documents uploaded meanwhile. Like the rest of the API, the
endpoints are plain `def` functions, and they never take a file system path from the request.
"""

import dataclasses
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, TypeVar

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from rag.api.errors import backend_failure
from rag.api.jobs import IngestJob, IngestJobs
from rag.api.schemas import (
    DeletionFailedResponse,
    DocumentListResponse,
    DocumentResponse,
    ErrorResponse,
    IngestConflictResponse,
    UploadedDocumentResponse,
)
from rag.config import Settings
from rag.dms.library import (
    DeletionFailed,
    DocumentExists,
    DocumentLibrary,
    DocumentNotFound,
    FileMissing,
    StorageFailed,
)
from rag.dms.repository import DocumentRecord
from rag.dms.storage import DocumentTooLarge, InvalidDocument, UnsafePath

logger = logging.getLogger(__name__)

T = TypeVar("T")
MB = 1024 * 1024
MULTIPART_OVERHEAD = 64 * 1024  # form fields and boundaries around the file itself
UNKNOWN_DOCUMENT = "Unknown document."
UNSAFE_FILE = ("The document's file can't be used safely on this server (its path or a link on the way isn't "
               "allowed); see the server log.")


def document_routes(settings: Settings, get_library: Callable[[], DocumentLibrary], jobs: IngestJobs,
                    start_indexing: Callable[[], IngestJob | None]) -> APIRouter:
    """`start_indexing()` starts an ingestion job if none is running and the services are up."""
    router = APIRouter(prefix="/documents", tags=["documents"])

    def library() -> DocumentLibrary:
        if not settings.database_url:
            raise HTTPException(status_code=503, detail="DATABASE_URL is not set, so documents can't be stored.")
        if not Path(settings.documents_dir).is_dir():
            raise HTTPException(status_code=500, detail="The documents folder (DOCUMENTS_DIR) doesn't exist on the server.")
        return _backend(get_library)

    @router.post(
        "",
        status_code=201,
        response_model=UploadedDocumentResponse,
        responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 413: {"model": ErrorResponse},
                   500: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    )
    def upload_document(
        response: Response,
        file: Annotated[UploadFile, File(description="A PDF with selectable text, or a Markdown or text file")],
        product: Annotated[str, Form()],
        version: Annotated[str, Form()],
        doc_type: Annotated[str, Form()],
    ) -> UploadedDocumentResponse:
        """Store a file in the documents folder under product/version/doc_type, list it as pending, and start
        indexing it.

        A file already stored at the same path is never replaced.
        """
        docs = library()
        try:
            document = _backend(docs.upload, file.file, file.filename or "", product, version, doc_type)
        except DocumentTooLarge as error:
            raise HTTPException(status_code=413, detail=str(error)) from None
        except InvalidDocument as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except DocumentExists as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except StorageFailed as error:
            raise HTTPException(status_code=500, detail=str(error)) from None
        except UnsafePath as error:
            raise _unsafe(error) from None
        logger.info("Uploaded document %s (%s)", document.id, document.source)
        job = start_indexing()
        response.headers["Location"] = f"/documents/{document.id}"
        return UploadedDocumentResponse(**dataclasses.asdict(document), ingest_job_id=job.id if job else None)

    @router.get("", response_model=DocumentListResponse, responses={503: {"model": ErrorResponse}})
    def list_documents() -> DocumentListResponse:
        """Every document in the library, most recently added first."""
        return DocumentListResponse(documents=[_body(d) for d in _backend(library().list)])

    @router.get("/{document_id}", response_model=DocumentResponse,
                responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
    def get_document(document_id: uuid.UUID) -> DocumentResponse:
        try:
            return _body(_backend(library().get, document_id))
        except DocumentNotFound:
            raise HTTPException(status_code=404, detail=UNKNOWN_DOCUMENT) from None

    @router.get("/{document_id}/file", response_class=FileResponse,
                responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
    def document_file(document_id: uuid.UUID, download: bool = False) -> FileResponse:
        """The original file: shown in the browser (PDF viewer, or plain text), or saved with ?download=true."""
        try:
            document, path = _backend(library().original_file, document_id)
        except DocumentNotFound:
            raise HTTPException(status_code=404, detail=UNKNOWN_DOCUMENT) from None
        except FileMissing:
            raise HTTPException(status_code=404, detail="The original file is missing on the server.") from None
        except UnsafePath as error:
            raise _unsafe(error) from None
        # Text is always sent as plain text, never HTML, so an uploaded file can't run script in the app.
        is_pdf = document.content_type == "application/pdf"
        media_type = document.content_type if is_pdf else "text/plain; charset=utf-8"
        return FileResponse(
            path, media_type=media_type, filename=document.filename,
            content_disposition_type="attachment" if download else "inline",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @router.delete(
        "/{document_id}",
        status_code=204,
        responses={404: {"model": ErrorResponse}, 409: {"model": IngestConflictResponse},
                   500: {"model": DeletionFailedResponse}, 503: {"model": DeletionFailedResponse}},
    )
    def delete_document(document_id: uuid.UUID) -> Response:
        """Permanently delete a document: its original file, its passages in the index and its record.

        Refused while an ingestion job runs. If it stops part-way, the response says what was done; deleting
        again finishes the job.
        """
        docs = library()
        running = jobs.running()
        if running is not None:
            return JSONResponse(
                status_code=409,
                content={"detail": "An ingestion job is running; delete the document when it has finished.",
                         "job_id": running.id},
            )
        try:
            _backend(docs.delete, document_id)
        except DocumentNotFound:
            raise HTTPException(status_code=404, detail=UNKNOWN_DOCUMENT) from None
        except DeletionFailed as failed:
            return _deletion_failed(failed)
        return Response(status_code=204)

    return router


class UploadTooLarge(HTTPException):
    def __init__(self, max_upload_mb: int):
        super().__init__(status_code=413, detail=f"The file is larger than the {max_upload_mb} MB limit.")


class UploadSizeLimit:
    """ASGI middleware capping the request body of an upload (POST /documents) at the size limit.

    The form parser spools uploaded files to a temporary file without any limit of its own, before the
    endpoint runs, so the cap has to be applied while the body is read. A declared Content-Length over the
    limit is refused before reading anything. Every upload, including a chunked one without Content-Length,
    is also counted as it arrives, and reading stops with a 413 as soon as the count passes the limit; the
    form parser then deletes its temporary files. The exact file size is checked again when it is saved.
    """

    def __init__(self, app: ASGIApp, max_upload_mb: int):
        self.app = app
        self.max_upload_mb = max_upload_mb
        self.limit = max_upload_mb * MB + MULTIPART_OVERHEAD

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not (scope["type"] == "http" and scope["method"] == "POST"
                and scope["path"].rstrip("/") == "/documents"):
            await self.app(scope, receive, send)
            return

        length = Headers(scope=scope).get("content-length")
        if length is not None and not length.isdigit():
            await JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header."})(
                scope, receive, send)
            return
        if length is not None and int(length) > self.limit:
            await JSONResponse(status_code=413, content={"detail": UploadTooLarge(self.max_upload_mb).detail})(
                scope, receive, send)
            return

        received = 0

        async def counted_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    # Raised inside the body parser; FastAPI passes an HTTPException through as the response.
                    raise UploadTooLarge(self.max_upload_mb)
            return message

        await self.app(scope, counted_receive, send)


def _backend(action: Callable[..., T], *args) -> T:
    """Run a library call, turning a database outage into a 503; other errors pass through."""
    try:
        return action(*args)
    except Exception as error:
        failure = backend_failure(error)
        if failure is None:
            raise
        logger.warning("Backend failure in the document library", exc_info=error)
        raise HTTPException(status_code=failure[0], detail=failure[1]) from None


def _unsafe(error: UnsafePath) -> HTTPException:
    logger.error("Refused an unsafe document path: %s", error)
    return HTTPException(status_code=500, detail=UNSAFE_FILE)


def _deletion_failed(failed: DeletionFailed) -> JSONResponse:
    steps = failed.steps
    if not steps.file_deleted and isinstance(failed.__cause__, UnsafePath):
        status, detail = 500, f"Nothing was deleted. {UNSAFE_FILE}"
    elif not steps.file_deleted:
        status, detail = 500, ("Nothing was deleted: the original file couldn't be removed (it may be open in "
                               "another program). Try again later.")
    else:
        failure = backend_failure(failed.__cause__) if failed.__cause__ else None
        status = failure[0] if failure else 500
        if not steps.chunks_deleted:
            detail = ("The original file was deleted, but its passages couldn't be removed from the index. "
                      "Delete the document again to finish; until then it stays listed.")
        else:
            detail = ("The original file and its passages were deleted, but the document couldn't be removed "
                      "from the list. Delete it again to finish.")
    return JSONResponse(status_code=status, content={"detail": detail, **dataclasses.asdict(steps)})


def _body(document: DocumentRecord) -> DocumentResponse:
    return DocumentResponse(**dataclasses.asdict(document))
