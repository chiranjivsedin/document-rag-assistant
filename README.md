# Document RAG Assistant

Ask questions in plain English about your product documentation (versions, release notes, deprecated features) and get answers taken only from your own documents, with numbered source citations. When the documents don't contain the answer, it replies "I don't know based on the available documents." instead of guessing.

Built with LangChain, local Ollama models, PostgreSQL with pgvector, a FastAPI HTTP API and a React web app.

## Status

A **local prototype**, tested on a developer laptop with fictional sample documents.

- **Pending:** deployment to Azure, and validation against real **version 16.2** documents (none have been indexed or tested yet).
- **Not built yet:** the release-notes / deprecated-features view, user login, Azure model providers, keyword search, and a relevance cut-off. Scanned PDFs (no text layer) and Word files are not supported.
- **Answers are slow on a laptop CPU** with the default model: about 20–65 seconds when a question needs new passages, a few seconds when it reuses the previous question's passages, and 15–30 seconds more when the model first has to load.

## Features

- **Indexing:** reads PDF (with a text layer), Markdown and text files from `documents/<product>/<version>/<doc_type>/`. Re-running skips unchanged files, replaces changed ones and removes deleted ones.
- **Answering:** finds the most relevant passages, optionally filtered by product and version, and generates an answer citing them as `[1]`, `[2]`, ….
- **Document library:** upload documents from the browser, see whether each one is indexed, open or download the original, and delete it (see [Document library](#document-library) below).
- **Command-line scripts:** `scripts/ingest.py` and `scripts/ask.py`.
- **HTTP API** (FastAPI):
  - `GET /health`: are Ollama, the models and the database reachable?
  - `POST /chat`: answer a question, with sources.
  - `POST /documents/ingest` and `GET /documents/ingest/{job_id}`: index the documents folder as a background job and follow it.
  - `/documents`: upload, list, open, download and delete documents.
- **Web app** (React):
  - **Chat:** ask with optional product, version and passage-count filters. Each answer shows its `[n]` citations as buttons that open the cited passage in full, with a strip of source cards marking which ones were cited.
  - **Sidebar:** New chat, this session's questions (click one to jump back to it), a live service-status card and a System / Light / Dark theme switch. On small screens it becomes a slide-out menu.
  - **Documents:** the document library: upload, search and filter, indexing status, open, download and delete.
  - **Ingestion:** starts indexing and shows progress and results.
  - **Release notes:** choose a version and a topic (what's new, deprecated or removed features); the question opens in Chat limited to that version. A dedicated release-notes view is not built yet.
- **Answer checks:** a reply that is only a citation (`[1]`) or only says the passages don't state the answer becomes the standard "I don't know" reply, and repeated citations are merged.

Everything runs locally: the models are served by Ollama on your machine, so documents and questions are not sent to a cloud service.

## Document library

The **Documents** screen and the `/documents` API manage the files the assistant answers from. It is a small library on purpose: no versions, approvals, check-in/check-out or permissions.

- **What can be uploaded:** one file at a time, `.pdf` (with selectable text), `.md` or `.txt`, up to `MAX_UPLOAD_MB` (default 50 MB). Each upload needs a **product**, **version** and **document type**: 1–64 letters, digits, dots, hyphens or underscores, starting and ending with a letter or digit (for example `documentum`, `16.2`, `release-notes`). The file must be what its extension says: a PDF that can be read (not damaged or password-protected), or UTF-8 text. Empty files are refused. The web app checks the same rules before sending; the server checks again.
- **Where files are stored:** in the documents folder the assistant already indexes, at `<product>/<version>/<doc_type>/<file name>`, so chat filters work on uploads straight away. An existing file is never overwritten: uploading to a name that is taken is refused. The file name is kept for display; on disk it is reduced to safe characters. Each document is also recorded in a `documents` table in PostgreSQL (status, size, fingerprint, dates).
- **Indexing:** an upload starts indexing by itself when no other indexing is running and Ollama and the database are up; otherwise it waits as **Pending** and is picked up by the next run. Statuses: **Pending**, **Processing**, **Indexed** (with its number of passages) and **Failed** (with the reason, for example a PDF without text). Failed documents are tried again by the next run. Files put in the documents folder by hand appear in the library too (as "found in the documents folder", without being copied) the first time the library is used after the server starts, and after each indexing run.
- **On the screen:** search by name, path, product, version or type; filter by status; **Open** shows the original in a new tab (PDF viewer, or plain text) and **Download** saves it; **Delete** asks for confirmation, then permanently removes the file, its passages from the index and its record. While a document is waiting, the list refreshes every few seconds; **Index pending documents** starts a run by hand.
- **Limits:** run the API as **one uvicorn worker** (never `--workers`), and stop the old process completely before starting a new one: indexing jobs and document statuses depend on a single process. Deleting is refused while indexing runs. If one indexing run fails (for example Ollama stops), every document it was indexing is marked failed with that reason. There is no upload progress bar and no login: keep the API on `127.0.0.1`.

The API is described in [the architecture guide](docs/langchain-ollama-rag-architecture.md#76-document-library), and the code in [under the hood](docs/under-the-hood.md#workflow-3-the-document-library-srcragdms).

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

- [Architecture and onboarding guide](docs/langchain-ollama-rag-architecture.md): components, data flow, configuration, the HTTP API and the document library.
- [Running it on Windows](docs/run-locally-windows.md): every command explained, including the web app and uploading documents.
- [Under the hood](docs/under-the-hood.md): ingestion, answering and the document library, file by file.

The guides cover the backend in depth; the web app's screens are described above.

## Project layout

```
src/rag/        Python package: ingestion, retrieval, generation, document library (dms/), service layer, HTTP API (api/)
scripts/        ingest.py and ask.py
frontend/       React + TypeScript web app (Vite)
tests/          unit/ (no services needed) and integration/ (live services)
docs/           guides
```

## Branches

- `integration`: the complete working application (backend and web app). New work branches from it and is merged back after review.
- `main`: lead-approved work only; currently the initial scaffold.
- Feature and fix branches (for example `feature/dms` for the document library, `fix/rag-answer-quality`, `docs/refresh-guides`) are merged into `integration` after review and kept for reference.

## Hosting

The application may later be hosted on an existing, underutilized Azure Windows Server VM, with PostgreSQL as its database. Nothing is deployed yet.
