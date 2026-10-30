# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

A working local prototype: LangChain + Ollama + pgvector in Docker, two command-line scripts, a FastAPI HTTP API over the same functions, and a React frontend in `frontend/` (app shell with sidebar, session history, status card and theme switch; Chat and Ingestion screens; Release notes only places version-scoped questions in Chat; Documents is a placeholder). Not built yet: mini DMS (document list/upload API and screens), the 16.2 release-notes view, authentication, Azure providers, deployment, hybrid (keyword) search, a relevance cut-off.

Branches:
- `integration`: the complete working application (backend + frontend). Create each new phase's branch from it (e.g. `feature/dms-api`), and merge the branch back after review and approval.
- `main`: lead-approved work only. It holds just the scaffold until the lead confirms requirements (VM, "16.2" product, mini DMS, hosting); `integration` is merged into it only then.
- `poc/langchain-ollama`, `feature/rest-api`, `feature/react-frontend`, `feature/ui-redesign`, `fix/rag-answer-quality`, `fix/sidebar-history-scroll`: earlier branches, each contained in `integration`; kept for reference.

## Running the prototype (Windows, from the repo root)

```bash
python -m venv .venv                                              # Python 3.14 works
.venv/Scripts/python.exe -m pip install -e ".[ollama,pgvector,dev,api]"
cp .env.example .env                                              # defaults work as-is locally
docker compose up -d                                              # PostgreSQL 18 + pgvector on 127.0.0.1:5433
ollama pull gemma3:4b && ollama pull nomic-embed-text             # Ollama must be running
.venv/Scripts/python.exe scripts/ingest.py                        # index documents/ (re-run any time)
.venv/Scripts/python.exe scripts/ask.py "What is deprecated?" --version 16.2   # optional --product, --k
.venv/Scripts/python.exe -m uvicorn rag.api.app:app --host 127.0.0.1 --port 8000   # HTTP API; docs at /docs
cd frontend && npm install && npm run dev                         # web app on http://localhost:5173 (needs the API)
```

- API endpoints (`src/rag/api/app.py`): `GET /health` (200 ok / 503 with `problems`), `POST /chat` (`{"question", "product"?, "version"?, "k"? 1–20}` → `answer`, `no_answer`, numbered `sources` with `excerpt`), `POST /documents/ingest` (no body; 202 + `job_id`, 409 if one is running), `GET /documents/ingest/{job_id}` (`running` / `succeeded` + `report` / `failed` + `error`; 404 if unknown). Errors are `{"detail": "..."}`: 422 invalid input, 503 Ollama/database unavailable, 502 model call failed, 500 generic (details only in the server log).

- Put files at `documents/<product>/<version>/<doc_type>/<file>` (PDF, `.md`, `.txt`), e.g. `documents/documentum/16.2/release-notes/notes.pdf`; product/version/doc_type come from those folders. Re-running ingest skips unchanged files, replaces changed ones and removes deleted ones. Scanned PDFs (no text layer) are skipped.
- On the 4-core laptop CPU, `gemma3:4b` takes ~20–65 s when a question needs new passages (mostly reading the prompt), a few seconds when the passages repeat, and 15–30 s more when the model has to load. `OLLAMA_KEEP_ALIVE_MINUTES` (default 30) keeps both models loaded between questions, holding ~5 GB of RAM.
- Tests: `pytest` (unit only, no services needed; API tests use FastAPI's `TestClient` with fakes) · `pytest -m integration` (needs Docker DB + Ollama; uses its own temporary tables and, for the API, a temporary documents folder) · single test: `pytest tests/unit/test_x.py::test_name`. Prefix with `.venv/Scripts/python.exe -m`. Add `-p no:warnings` to hide a harmless `langsmith` deprecation warning on Python 3.14. Frontend: `cd frontend && npm run typecheck && npm test && npm run build`.

## Layout and conventions

- src-layout package `rag` (`src/rag/`); import as `from rag.ingestion import ...`, never `from src...`. Flow: `ingestion/` (loaders → chunking → pipeline) → vector store → `retrieval/search.py` (vector search + product/version filter) → `generation/` (prompt + LLM, `[n]` citations, fixed "I don't know" reply; `citations.py` merges repeated citations and turns citation-only or "not stated in the passages" replies into that reply, because `no_answer` and hidden sources depend on the exact text). Prompt wording was tuned against live `gemma3:4b` runs: re-check answers with the real model after changing it. Entry points: `scripts/ingest.py`, `scripts/ask.py` and the API (`src/rag/api/`). All of them go through `src/rag/service.py` (`build_services()`, `run_ingest()`, `ask()`, `sources_to_show()`); put shared glue there, not in a script or an endpoint.
- Keep third-party frameworks (e.g. LangChain) inside these modules behind our own functions (`ingest()`, `retrieve()`, `answer()`), so scripts, the API and tests don't depend on them. Unit tests use fakes (fake store, `FakeListChatModel`).
- API conventions: endpoints are plain `def` (FastAPI runs them in a thread pool; the vector store's sync calls rely on that), never `async def`. Services are built on first use, not at startup, so the server starts while Docker/Ollama are down. Backend exceptions are classified by `providers.unavailable_errors()` / `model_errors()`, so the API never imports Ollama/SQLAlchemy itself. Ingestion only ever reads `DOCUMENTS_DIR`, never a path from a request. Ingestion jobs live in memory (`api/jobs.py`, one lock, one job at a time): history is lost on restart, so run a single uvicorn worker.
- On this branch the `chunks` table is created by `providers.get_vector_store()` (no migrations); `src/rag/database.py` holds the psycopg helpers (connections, URL conversion, `table_exists`, `indexed_files`). Don't add empty placeholder modules: create a module when it has code. Changing `METADATA_COLUMNS` or the embedding size needs the table dropped and documents re-ingested (`docker compose down -v` wipes the local DB).
- Dependencies are declared in `pyproject.toml` (there is no requirements.txt), with optional groups `ollama`, `pgvector`, `api` (FastAPI, uvicorn) and `dev` (pytest, httpx for `TestClient`). Don't use `langchain-community` (discontinued); load files with `pypdf` and plain Python into `langchain_core` `Document`s.
- Model backends and the vector store are built only in `src/rag/providers.py`; everything else uses LangChain's standard interfaces. Vector store = `langchain_postgres.PGVectorStore` (v2) on a table we define, with metadata as real columns (`METADATA_COLUMNS`) so filters are SQL; it connects via `asyncpg` (`async_database_url()`), never the deprecated `PGVector` class.
- Local database: `docker compose up -d` → PostgreSQL 18 + pgvector on `127.0.0.1:5433` (user/db `rag`). Always write `127.0.0.1`, not `localhost`, in database and Ollama URLs: on Windows `localhost` tries IPv6 `::1` first and stalls (database) or adds ~2 s per call (Ollama via urllib).
- Scripts (and the API's `/health`, `/chat` and `/documents/ingest`) run `rag.health.check_services()` first, so a stopped Docker/Ollama, a missing model or a bad `DATABASE_URL` ends with one clear line (or a 503) instead of a traceback.
- Config comes from `.env` via `rag.config.get_settings()`; `.env.example` lists the keys and defaults. `documents/` holds client files and is git-ignored.

## Hosting (planned, not built)

The application may later be hosted on an existing, underutilized Azure Windows Server VM, with PostgreSQL as its database. That machine is shared infrastructure, not part of this project; its access and configuration details are kept out of this repository (in a git-ignored `CLAUDE.local.md` on the developer's machine).
