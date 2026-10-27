# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Scaffold only: modules under `src/rag/` are docstring stubs. The database will be PostgreSQL, possibly on an existing Azure VM reused for hosting (see below). Open decisions that shape the code: pgvector on the Windows server, embedding model (fixes the vector dimension), LLM provider, and whether LangChain is adopted.

## Layout and commands

- src-layout package `rag` (`src/rag/`); import as `from rag.ingestion import ...`, never `from src...`. Pipeline: `ingestion/` (load → chunk → embed → store) → `database/` → `retrieval/` (vector + full-text) → `generation/` (prompt + LLM) → `api/`. `scripts/ingest.py` and `scripts/ask.py` are CLI entry points for testing the pipeline without the API.
- Keep third-party frameworks (e.g. LangChain) inside these modules behind our own functions, so the API and tests don't depend on them.
- Schema lives in `src/rag/database/migrations/NNN_*.sql` (numbered, applied in order). If LangChain's PGVector is adopted it manages its own tables — don't duplicate them.
- Dependencies are declared in `pyproject.toml` (there is no requirements.txt), with optional groups `ollama` and `dev`. Setup: `.venv/Scripts/python.exe -m pip install -e ".[ollama,dev]"`. Don't use `langchain-community` (discontinued); load files with `pypdf` and plain Python into `langchain_core` `Document`s.
- Model backends and the vector store are built only in `src/rag/providers.py`; everything else uses LangChain's standard interfaces. Vector store = `langchain_postgres.PGVectorStore` (v2) on a table we define, with metadata as real columns (`METADATA_COLUMNS`) so filters are SQL; it connects via `asyncpg` (`async_database_url()`), never the deprecated `PGVector` class.
- Local database: `docker compose up -d` → PostgreSQL 18 + pgvector on `127.0.0.1:5433` (user/db `rag`). Always write `127.0.0.1`, not `localhost`, in database URLs: on Windows `localhost` tries IPv6 `::1` first and stalls.
- Config comes from `.env` via `rag.config.get_settings()`; `.env.example` lists the keys. `documents/` holds client files and is git-ignored.
- Tests: `pytest` (unit only; integration tests are deselected by default) · `pytest -m integration` (needs the SSH tunnel to the DB) · single test: `pytest tests/unit/test_x.py::test_name`.

## Hosting (planned, not built)

The application may later be hosted on an existing, underutilized Azure Windows Server VM, with PostgreSQL as its database. That machine is shared infrastructure, not part of this project; its access and configuration details are kept out of this repository (in a git-ignored `CLAUDE.local.md` on the developer's machine).
