# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Branch `poc/langchain-ollama`: a working local prototype (LangChain + Ollama + pgvector in Docker), command-line only. `main` holds only the scaffold until the lead confirms requirements (VM, "16.2" product, mini DMS, hosting); then this branch is merged or redone. Not built yet: mini DMS, HTTP API/UI, Azure providers, hybrid (keyword) search, a relevance cut-off.

## Running the prototype (Windows, from the repo root)

```bash
python -m venv .venv                                              # Python 3.14 works
.venv/Scripts/python.exe -m pip install -e ".[ollama,pgvector,dev]"
cp .env.example .env                                              # defaults work as-is locally
docker compose up -d                                              # PostgreSQL 18 + pgvector on 127.0.0.1:5433
ollama pull gemma3:4b && ollama pull nomic-embed-text             # Ollama must be running
.venv/Scripts/python.exe scripts/ingest.py                        # index documents/ (re-run any time)
.venv/Scripts/python.exe scripts/ask.py "What is deprecated?" --version 16.2   # optional --product, --k
```

- Put files at `documents/<product>/<version>/<doc_type>/<file>` (PDF, `.md`, `.txt`), e.g. `documents/documentum/16.2/release-notes/notes.pdf`; product/version/doc_type come from those folders. Re-running ingest skips unchanged files, replaces changed ones and removes deleted ones. Scanned PDFs (no text layer) are skipped.
- On the 4-core laptop CPU, `gemma3:4b` takes ~15–30 s per answer; the first call after a while also loads the model.
- Tests: `pytest` (unit only, no services needed) · `pytest -m integration` (needs Docker DB + Ollama; uses its own temporary tables) · single test: `pytest tests/unit/test_x.py::test_name`. Prefix with `.venv/Scripts/python.exe -m`. Add `-p no:warnings` to hide a harmless `langsmith` deprecation warning on Python 3.14.

## Layout and conventions

- src-layout package `rag` (`src/rag/`); import as `from rag.ingestion import ...`, never `from src...`. Flow: `ingestion/` (loaders → chunking → pipeline) → vector store → `retrieval/search.py` (vector search + product/version filter) → `generation/` (prompt + LLM, `[n]` citations, fixed "I don't know" reply). `scripts/ingest.py` and `scripts/ask.py` are the entry points; there is no HTTP API yet.
- Keep third-party frameworks (e.g. LangChain) inside these modules behind our own functions (`ingest()`, `retrieve()`, `answer()`), so scripts, a future API and tests don't depend on them. Unit tests use fakes (fake store, `FakeListChatModel`).
- On this branch the `chunks` table is created by `providers.get_vector_store()` (no migrations); `src/rag/database.py` holds the psycopg helpers (connections, URL conversion, `table_exists`, `indexed_files`). Don't add empty placeholder modules: create a module when it has code. Changing `METADATA_COLUMNS` or the embedding size needs the table dropped and documents re-ingested (`docker compose down -v` wipes the local DB).
- Dependencies are declared in `pyproject.toml` (there is no requirements.txt), with optional groups `ollama`, `pgvector` and `dev`. Don't use `langchain-community` (discontinued); load files with `pypdf` and plain Python into `langchain_core` `Document`s.
- Model backends and the vector store are built only in `src/rag/providers.py`; everything else uses LangChain's standard interfaces. Vector store = `langchain_postgres.PGVectorStore` (v2) on a table we define, with metadata as real columns (`METADATA_COLUMNS`) so filters are SQL; it connects via `asyncpg` (`async_database_url()`), never the deprecated `PGVector` class.
- Local database: `docker compose up -d` → PostgreSQL 18 + pgvector on `127.0.0.1:5433` (user/db `rag`). Always write `127.0.0.1`, not `localhost`, in database and Ollama URLs: on Windows `localhost` tries IPv6 `::1` first and stalls (database) or adds ~2 s per call (Ollama via urllib).
- Scripts run `rag.health.check_services()` first, so a stopped Docker/Ollama, a missing model or a bad `DATABASE_URL` ends with one clear line instead of a traceback.
- Config comes from `.env` via `rag.config.get_settings()`; `.env.example` lists the keys and defaults. `documents/` holds client files and is git-ignored.

## Hosting (planned, not built)

The application may later be hosted on an existing, underutilized Azure Windows Server VM, with PostgreSQL as its database. That machine is shared infrastructure, not part of this project; its access and configuration details are kept out of this repository (in a git-ignored `CLAUDE.local.md` on the developer's machine).
