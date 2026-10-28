"""HTTP API over the RAG prototype.

Run: python -m uvicorn rag.api.app:app --host 127.0.0.1 --port 8000   (interactive docs at /docs)

Endpoints are plain `def` functions: FastAPI runs them in a thread pool, so a slow model call doesn't
block other requests, and the synchronous vector-store calls work exactly as they do in the scripts.
"""

import logging
import threading
import time

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core.documents import Document

from rag.api.schemas import ChatRequest, ChatResponse, ChatSource, ErrorResponse, HealthResponse
from rag.config import Settings, get_settings
from rag.generation.prompts import NO_ANSWER
from rag.health import check_services
from rag.providers import model_errors, unavailable_errors
from rag.service import Services, ask, build_services

logger = logging.getLogger(__name__)

EXCERPT_CHARS = 300


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
        except unavailable_errors() as error:
            logger.warning("Backend became unavailable during /chat", exc_info=error)
            raise HTTPException(
                status_code=503, detail="Ollama or the database stopped responding. Check GET /health."
            ) from None
        except model_errors() as error:
            logger.warning("Model call failed during /chat", exc_info=error)
            reason = getattr(error, "error", None) or type(error).__name__  # Ollama's own reason when it gives one
            raise HTTPException(status_code=502, detail=f"The model service failed: {str(reason)[:300]}") from None

        return ChatResponse(
            answer=result.text,
            no_answer=NO_ANSWER in result.text,
            sources=[_source(number, doc) for number, doc in enumerate(result.sources, 1)],
            model=settings.llm_model,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )

    return app


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


app = create_app()
