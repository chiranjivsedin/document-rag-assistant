# Document RAG Assistant

Ask questions in plain English about your product documentation (versions, release notes, deprecated features) and get answers taken only from your own documents, with numbered source citations. When the documents don't contain the answer, it replies "I don't know based on the available documents." instead of guessing.

Built with LangChain, local Ollama models, PostgreSQL with pgvector, a FastAPI HTTP API and a React web app.

## Status

A **local prototype**, tested on a developer laptop with fictional sample documents.

- **Pending:** deployment to Azure, and validation against real **version 16.2** documents (none have been indexed or tested yet).
- **Not built yet:** the mini document-management screens (upload and manage documents), the release-notes / deprecated-features view, user login, Azure model providers, keyword search, and a relevance cut-off. Scanned PDFs (no text layer) and Word files are not supported.
- **Answers are slow on a laptop CPU:** a minute or more per answer with the default model.

## Features

- **Indexing:** reads PDF (with a text layer), Markdown and text files from `documents/<product>/<version>/<doc_type>/`. Re-running skips unchanged files, replaces changed ones and removes deleted ones.
- **Answering:** finds the most relevant passages, optionally filtered by product and version, and generates an answer citing them as `[1]`, `[2]`, ….
- **Command-line scripts:** `scripts/ingest.py` and `scripts/ask.py`.
- **HTTP API** (FastAPI):
  - `GET /health`: are Ollama, the models and the database reachable?
  - `POST /chat`: answer a question, with sources.
  - `POST /documents/ingest` and `GET /documents/ingest/{job_id}`: index the documents folder as a background job and follow it.
- **Web app** (React): a Chat screen with clickable citations and a service-status banner, and an Ingestion screen that starts indexing and shows progress and results. The Documents and Release notes tabs are placeholders.

Everything runs locally: the models are served by Ollama on your machine, so documents and questions are not sent to a cloud service.

## Requirements

- Python 3.10 or newer (tested with 3.14)
- Docker Desktop, for the local PostgreSQL 18 + pgvector database
- [Ollama](https://ollama.com), running, with the `gemma3:4b` and `nomic-embed-text` models
- Node.js 22.12 or newer, for the web app (tested with 22.23)

## Quick start (Windows, from the repository root)

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[ollama,pgvector,dev,api]"
cp .env.example .env                       # the defaults work as-is locally
docker compose up -d                       # PostgreSQL 18 + pgvector on 127.0.0.1:5433
ollama pull gemma3:4b
ollama pull nomic-embed-text
```

Put your files in `documents/<product>/<version>/<doc_type>/` (this folder is git-ignored), then:

```bash
.venv/Scripts/python.exe scripts/ingest.py
.venv/Scripts/python.exe scripts/ask.py "Which features are deprecated?" --version 16.2   # optional: --product, --k
```

HTTP API (interactive docs at http://127.0.0.1:8000/docs):

```bash
.venv/Scripts/python.exe -m uvicorn rag.api.app:app --host 127.0.0.1 --port 8000
```

Web app, with the API running (open http://localhost:5173):

```bash
cd frontend
npm install
npm run dev
```

There is no login yet, so keep the API on `127.0.0.1`. For a step-by-step PowerShell walkthrough, see [docs/run-locally-windows.md](docs/run-locally-windows.md).

## Tests

```bash
.venv/Scripts/python.exe -m pytest                  # unit tests: no services needed
.venv/Scripts/python.exe -m pytest -m integration   # live tests: need the Docker database and Ollama
cd frontend && npm run typecheck && npm test && npm run build
```

## Documentation

- [Architecture and onboarding guide](docs/langchain-ollama-rag-architecture.md): components, data flow, configuration and the HTTP API.
- [Running it on Windows](docs/run-locally-windows.md): every command explained.
- [Under the hood](docs/under-the-hood.md): ingestion and answering, file by file.

These guides cover the scripts and the API; the web app is described in this README for now.

## Project layout

```
src/rag/        Python package: ingestion, retrieval, generation, service layer, HTTP API (api/)
scripts/        ingest.py and ask.py
frontend/       React + TypeScript web app (Vite)
tests/          unit/ (no services needed) and integration/ (live services)
docs/           guides
```

## Branches

- `integration`: the complete working application (backend and web app). New work branches from it and is merged back after review.
- `main`: lead-approved work only; currently the initial scaffold.
- `poc/langchain-ollama`, `feature/rest-api`, `feature/react-frontend`: earlier phase branches, kept for reference.

## Hosting

The application may later be hosted on an existing, underutilized Azure Windows Server VM, with PostgreSQL as its database. Nothing is deployed yet.
