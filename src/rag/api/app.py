"""HTTP API over the RAG prototype.

Run: python -m uvicorn rag.api.app:app --host 127.0.0.1 --port 8000   (interactive docs at /docs)

Endpoints are plain `def` functions: FastAPI runs them in a thread pool, so a slow model call doesn't
block other requests, and the synchronous vector-store calls work exactly as they do in the scripts.
Ingestion jobs are tracked in memory (see rag.api.jobs): run a single worker process, and expect job
history to be gone after a restart.
"""

import logging
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core.documents import Document

from rag.api.jobs import IngestJob, IngestJobs, JobAlreadyRunning
from rag.api.schemas import (
    ChatRequest,
    ChatResponse,
    ChatSource,
    ErrorResponse,
    HealthResponse,
    IngestConflictResponse,
    IngestJobResponse,
    IngestReportBody,
)
from rag.config import Settings, get_settings
from rag.generation.prompts import NO_ANSWER
from rag.health import check_services
from rag.providers import model_errors, unavailable_errors
from rag.service import Services, ask, build_services, run_ingest

logger = logging.getLogger(__name__)

EXCERPT_CHARS = 300
UNAVAILABLE = "Ollama or the database stopped responding. Check GET /health."
INGEST_FAILED = "Ingestion failed unexpectedly; see the server log."


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    """Build the app. `services` can be passed in prebuilt (tests); otherwise they are built on first use."""
    settings = settings or get_settings()
    app = FastAPI(title="RAG prototype API", version="0.1.0")
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.api_cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    # Built on the first request that needs them, not at startup, so the API (and /health) still
    # starts while Docker or Ollama is down. A failed build isn't cached; the next request retries.
    built = {"services": services}
    build_lock = threading.Lock()

    def get_services() -> Services:
        with build_lock:
            if built["services"] is None:
                built["services"] = build_services(settings)
            return built["services"]

    jobs = IngestJobs()

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        # Details go to the server log only; clients never see internals such as connection strings.
        logger.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=error)
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})

    @app.get("/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}})
    def health(response: Response) -> HealthResponse:
        """Whether Ollama (with both models) and the database are reachable: 200 if so, 503 if not."""
        problems = check_services(settings, need_chat_model=True)
        if problems:
            response.status_code = 503
        return HealthResponse(
            status="unavailable" if problems else "ok",
            llm_model=settings.llm_model,
            embedding_model=settings.embedding_model,
            problems=problems,
        )

    @app.post(
        "/chat",
        response_model=ChatResponse,
        responses={502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    )
    def chat(request: ChatRequest) -> ChatResponse:
        """Answer a question from the indexed documents, optionally limited to a product and/or version."""
        problems = check_services(settings, need_chat_model=True)
        if problems:
            raise HTTPException(status_code=503, detail=" ".join(problems))

        started = time.monotonic()
        try:
            result = ask(get_services(), request.question, k=request.k,
                         version=request.version, product=request.product)
        except Exception as error:
            failure = _backend_failure(error)
            if failure is None:
                raise
            logger.warning("Backend failure during /chat", exc_info=error)
            raise HTTPException(status_code=failure[0], detail=failure[1]) from None

        return ChatResponse(
            answer=result.text,
            no_answer=NO_ANSWER in result.text,
            sources=[_source(number, doc) for number, doc in enumerate(result.sources, 1)],
            model=settings.llm_model,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )

    @app.post(
        "/documents/ingest",
        status_code=202,
        response_model=IngestJobResponse,
        responses={409: {"model": IngestConflictResponse}, 500: {"model": ErrorResponse},
                   503: {"model": ErrorResponse}},
    )
    def start_ingest(response: Response) -> IngestJobResponse | JSONResponse:
        """Index the configured documents folder in a background job; poll the job until it finishes.

        Takes no input: the folder always comes from DOCUMENTS_DIR, never from the request.
        """
        documents_dir = Path(settings.documents_dir)
        if not documents_dir.is_dir():
            raise HTTPException(status_code=500, detail="The documents folder (DOCUMENTS_DIR) doesn't exist on the server.")
        problems = check_services(settings, need_chat_model=False)
        if problems:
            raise HTTPException(status_code=503, detail=" ".join(problems))

        try:
            job = jobs.start()
        except JobAlreadyRunning as running:
            return JSONResponse(
                status_code=409,
                content={"detail": "An ingestion job is already running.", "job_id": running.job.id},
            )
        try:
            threading.Thread(target=run_job, args=(job.id, documents_dir), name=f"ingest-{job.id[:8]}",
                             daemon=True).start()
        except Exception:
            jobs.fail(job.id, INGEST_FAILED)
            raise
        response.headers["Location"] = f"/documents/ingest/{job.id}"
        return _job_body(job)

    def run_job(job_id: str, documents_dir: Path) -> None:
        try:
            report = run_ingest(get_services(), documents_dir)
        except Exception as error:
            logger.error("Ingestion job %s failed", job_id, exc_info=error)
            failure = _backend_failure(error)
            jobs.fail(job_id, failure[1] if failure else INGEST_FAILED)
        else:
            jobs.succeed(job_id, report)

    @app.get(
        "/documents/ingest/{job_id}",
        response_model=IngestJobResponse,
        responses={404: {"model": ErrorResponse}},
    )
    def ingest_status(job_id: str) -> IngestJobResponse:
        """A job's state: running, then succeeded (with its report) or failed (with a reason)."""
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=404,
                detail="Unknown ingestion job. Job history is kept in memory and is lost when the server restarts.",
            )
        return _job_body(job)

    return app


def _backend_failure(error: Exception) -> tuple[int, str] | None:
    """HTTP status and client-safe message for a backend failure, or None if it isn't one."""
    if isinstance(error, unavailable_errors()):
        return 503, UNAVAILABLE
    if isinstance(error, model_errors()):
        reason = getattr(error, "error", None) or type(error).__name__  # Ollama's own reason when it gives one
        return 502, f"The model service failed: {str(reason)[:300]}"
    return None


def _source(number: int, doc: Document) -> ChatSource:
    meta = doc.metadata
    return ChatSource(
        number=number,
        source=meta.get("source"),
        product=meta.get("product"),
        version=meta.get("version"),
        doc_type=meta.get("doc_type"),
        page=meta.get("page"),
        distance=meta["distance"],
        excerpt=doc.page_content[:EXCERPT_CHARS],
    )


def _job_body(job: IngestJob) -> IngestJobResponse:
    report = job.report
    return IngestJobResponse(
        job_id=job.id,
        status=job.status,
        started_at=job.started_at,
        finished_at=job.finished_at,
        report=None if report is None else IngestReportBody(
            added=report.added,
            updated=report.updated,
            unchanged=report.unchanged,
            removed=report.removed,
            no_text=report.no_text,
            chunks_stored=report.chunks_stored,
        ),
        error=job.error,
    )


app = create_app()
