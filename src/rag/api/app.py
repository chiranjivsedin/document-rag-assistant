"""HTTP API over the RAG prototype.

Run: python -m uvicorn rag.api.app:app --host 127.0.0.1 --port 8000   (interactive docs at /docs)

Endpoints are plain `def` functions: FastAPI runs them in a thread pool, so a slow model call doesn't
block other requests, and the synchronous vector-store calls work exactly as they do in the scripts.
"""

import logging

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from rag.api.schemas import HealthResponse
from rag.config import Settings, get_settings
from rag.health import check_services

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="RAG prototype API", version="0.1.0")
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.api_cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

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

    return app


app = create_app()
