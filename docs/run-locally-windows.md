# Running the prototype manually on Windows

A step-by-step guide to running the local RAG prototype (branch `integration`) by hand, with what each command does: the command-line scripts, the HTTP API and the web app. For how the pieces fit together, see [langchain-ollama-rag-architecture.md](langchain-ollama-rag-architecture.md).

Commands are written for **PowerShell**. Steps 1–4 are one-time setup; after that you only need steps 5–12.

## Before you start: four programs

| Program | Why | Check it with |
|---|---|---|
| **Python 3.10+** | Runs the app | `python --version` |
| **Docker Desktop** | Runs the PostgreSQL database | Docker Desktop open, whale icon in the system tray |
| **Ollama** | Runs the AI models on your laptop | `ollama --version` |
| **Node.js 22.12+** | Runs the web app (step 9 only) | `node --version` |

## 1. Go to the project and the right branch

```powershell
cd <path-to-your-clone>\RAG
git switch integration
```

- `cd` moves into the project folder. Every later command assumes you are here.
- `git switch` selects `integration`, the branch with the complete working application: scripts, HTTP API and web app. (`main` only has the initial scaffold; the other branches are earlier steps, all included in `integration`.)

## 2. Create the Python environment (one-time)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[ollama,pgvector,dev,api]"
```

- `python -m venv .venv` creates a private Python environment in the `.venv` folder, so this project's packages don't mix with other projects. Skip it if `.venv` already exists.
- `pip install -e ".[ollama,pgvector,dev,api]"` installs the app and its libraries:
  - `-e` means "editable": your code changes take effect without reinstalling.
  - `[ollama,pgvector,dev,api]` adds the optional groups: the Ollama connection, the PostgreSQL vector store, pytest and httpx for tests, and FastAPI and uvicorn for the HTTP API.
  - If you installed before the API existed, run this line again to add the `api` group.

Every command calls `.\.venv\Scripts\python.exe` directly, so you never need to "activate" the environment. That avoids PowerShell's script-permission errors.

## 3. Create your settings file (one-time)

```powershell
Copy-Item .env.example .env
```

- This copies the settings template to `.env`, which the app reads at startup. The defaults already point at the local database and Ollama, so you don't need to edit anything.
- Skip it if `.env` already exists.

## 4. Download the two AI models (one-time)

```powershell
ollama pull gemma3:4b
ollama pull nomic-embed-text
```

- `gemma3:4b` is the chat model that writes answers (about 3.3 GB).
- `nomic-embed-text` turns text into 768 numbers so passages with similar meaning can be found (about 270 MB).
- If a model is already downloaded and up to date, `pull` just confirms that. `ollama list` shows what is installed.

> **Git Bash instead of PowerShell?** Use forward slashes (`.venv/Scripts/python.exe`) and `cp .env.example .env`. Everything else is the same.

## 5. Start the services (every session)

1. **Start Docker Desktop** and wait until it says it is running.
2. **Start Ollama** from the Start menu. It runs in the system tray.
3. Start the database:

```powershell
docker compose up -d
docker compose ps
```

- `docker compose up -d` starts the database defined in `docker-compose.yml`: PostgreSQL 18 with pgvector, reachable only from your laptop at `127.0.0.1:5433`. `-d` runs it in the background. Your data is kept between restarts.
- `docker compose ps` shows its status. Wait until it says `(healthy)`, usually within a few seconds.

**Memory:** the two models need about 5 GB of free RAM. After a question they stay loaded for 30 minutes (`OLLAMA_KEEP_ALIVE_MINUTES` in `.env`), so the next answer doesn't wait for them to load again; lower it if your laptop is short of memory. If you see "model requires more system memory", close some apps or restart Ollama.

## 6. Add documents and index them

Put files in `documents\<product>\<version>\<doc_type>\`, for example:

```
documents\documentum\16.2\release-notes\notes.pdf
```

- The folder names become the product, version and document type, which you can filter by when asking.
- Supported files: PDF (with selectable text), `.md` and `.txt`. Scanned PDFs without a text layer are skipped.
- `documents/` is git-ignored, so client files never get committed. Three fictional "Acme DMS" sample files (versions 1.0 and 2.0) may already be there for testing.

Then:

```powershell
.\.venv\Scripts\python.exe scripts\ingest.py
```

- This reads every file, splits it into chunks, turns each chunk into numbers with `nomic-embed-text`, and stores everything in the database.
- It ends with a summary such as `Done in 5.5s: 3 added, 0 updated, 0 unchanged, 0 removed, 3 chunks stored.`
- Re-run it whenever you add, change or delete files. Unchanged files are skipped, changed ones are replaced and deleted ones are removed, so repeat runs are quick.
- To index a different folder, add `--documents-dir D:\some\folder`.

## 7. Ask a question

```powershell
.\.venv\Scripts\python.exe scripts\ask.py "Which features are deprecated?" --version 1.0
```

- **The question** goes in quotes.
- **`--version 1.0`** (optional) only uses documents from that version's folder. `--product acme-dms` does the same for product. `--k 3` changes how many passages it retrieves (default 5).
- **What happens:** it finds the passages closest in meaning, sends them to `gemma3:4b` with strict instructions to answer only from them, and prints the answer as it is written. Then it lists the **Sources**, where `[1]` in the answer is `[1]` in the list.
- **Time on a laptop CPU:** about 20–65 seconds when the question needs new passages (the model reads them first), a few seconds when it reuses the previous question's passages, and 15–30 seconds more when the model has to load: the first question after Ollama starts, or after 30 idle minutes.
- **Unanswerable questions:** if the documents don't contain the answer, it replies `I don't know based on the available documents.` A reply that is only a citation such as `[1]`, or only says the passages don't state the answer, is treated the same way.

## 8. Run the HTTP API (optional)

The API does the same as steps 6 and 7, but over HTTP, so other programs (the web app in step 9, later the mini DMS) can use it. Docker and Ollama must be running as in step 5.

```powershell
.\.venv\Scripts\python.exe -m uvicorn rag.api.app:app --host 127.0.0.1 --port 8000
```

- `uvicorn` is the web server; `rag.api.app:app` points it at the FastAPI app in `src\rag\api\app.py`.
- `--host 127.0.0.1` accepts connections only from your own machine. The API has no login, so keep it that way.
- `--port 8000` is the port: the API is at `http://127.0.0.1:8000`.
- Start it from the project folder: `DOCUMENTS_DIR` (default `documents`) is looked up from where you start it.
- The window stays busy while the server runs and shows one line per request. Press **Ctrl+C** to stop it.

Open **http://127.0.0.1:8000/docs** in a browser to see every endpoint and try it ("Try it out", then "Execute"). From a second PowerShell window you can also call it directly:

```powershell
# Are Ollama and the database reachable? (status "ok", or an error listing the problems)
Invoke-RestMethod http://127.0.0.1:8000/health

# Ask a question (same filters as ask.py)
$body = @{ question = "Which features are deprecated?"; version = "1.0" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/chat -ContentType "application/json" -Body $body

# Index documents\ in the background, then check on the job until its status is no longer "running"
$job = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/documents/ingest
Invoke-RestMethod "http://127.0.0.1:8000/documents/ingest/$($job.job_id)"
```

- **`/health`** returns `status`, the two model names and a list of `problems` (empty when all is well).
- **`/chat`** takes `question` (required), `product`, `version` and `k` (1–20, default 5). It returns `answer`, `no_answer` (true for "I don't know"), and `sources`, each with a `number` matching the `[n]` in the answer and an `excerpt` (the whole passage the model was given). The whole answer arrives at once, after the same time as in step 7.
- **`/documents/ingest`** always indexes `DOCUMENTS_DIR`; it doesn't take a folder from the request. It answers straight away with a `job_id`. Checking that job shows `running`, then `succeeded` with the same counts `ingest.py` prints, or `failed` with a reason. Only one job runs at a time: starting a second one returns an error naming the running job.
- **Job history is kept in memory only.** After you stop or restart the server, old job ids are unknown (404), and a job that was running when you stopped it never finishes. Files it had already indexed stay indexed; run ingestion again to finish the rest.
- **Errors** come back as `{"detail": "..."}` and PowerShell shows them in red: 422 for a bad request (for example `k` above 20), 503 when Ollama or the database is down (same messages as step 10 below), 502 when the model itself fails (for example not enough memory), 500 for anything unexpected.

## 9. Run the web app (optional)

The web app is a browser interface over the HTTP API, so start the API first (step 8) and leave it running. In a second PowerShell window:

```powershell
cd frontend
npm install
npm run dev
```

- `npm install` downloads the web app's libraries into `frontend\node_modules`. It is needed once, and again after the app's dependencies change.
- `npm run dev` starts the development server. Open **http://localhost:5173** in a browser. It forwards the app's requests to the API at `127.0.0.1:8000`, so no extra setup is needed.
- **Chat:** type a question, optionally with a product, version and number of passages, and press Enter. Click a `[n]` citation or a source card to read the full passage it came from.
- **Sidebar:** New chat, this session's questions (click one to jump back to it), a service-status card with a re-check button, and a System / Light / Dark theme switch. When a service is down, Chat also shows a notice saying which one and why.
- **Ingestion:** the same indexing as step 6, with progress and results. **Release notes:** pick a version and a topic to place a version-scoped question in Chat.
- Press **Ctrl+C** in this window to stop the web app.

## 10. If something isn't running

Both scripts check the services first and stop with one clear line instead of a long error (the API returns the same lines with status 503):

| Message starts with | Meaning | Fix |
|---|---|---|
| `Can't reach Ollama at http://127.0.0.1:11434` | Ollama isn't running | Start Ollama |
| `Ollama model '...' is not installed` | A model is missing | Run the `ollama pull ...` command it prints |
| `Can't connect to the database at 127.0.0.1:5433` | Docker or the database container isn't running | Start Docker Desktop, then `docker compose up -d` |
| `... password authentication failed ...` | Wrong user or password | Check `DATABASE_URL` in `.env` |
| `DATABASE_URL is not set` | No `.env` file | Do step 3 |
| `Documents folder not found` | Wrong or missing folder | Create `documents\` or fix `--documents-dir` |

Fix what it names and run the command again.

Always write `127.0.0.1`, not `localhost`, in `.env` URLs: on Windows `localhost` tries IPv6 first, which stalls the database connection and slows down Ollama calls.

## 11. Run the tests (optional)

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:warnings
.\.venv\Scripts\python.exe -m pytest -m integration -p no:warnings
cd frontend; npm run typecheck; npm test; npm run build; cd ..
```

- The first runs the **unit tests**, including the API's. They use stand-ins, so Docker and Ollama aren't needed, and they take well under a minute. Integration tests are skipped in this run.
- The second runs only the **integration tests** against the real database and Ollama. Expect a few minutes, mostly waiting for answers. They use their own temporary tables (and, for the API test, their own temporary documents folder), so your indexed data isn't touched. If Docker or Ollama isn't running, they fail rather than skip.
- `-p no:warnings` hides a harmless warning from a LangChain dependency on Python 3.14.
- To run one test: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_chunking.py::<test_name>`.
- The last line checks the web app: type check, its tests (no services needed) and a production build.

## 12. When you're done

```powershell
docker compose down
```

- If the web app or the API is running, stop them first with **Ctrl+C** in their windows.
- This stops the database and frees memory. Your indexed data is kept.
- `docker compose down -v` would delete the data too, and you'd need to re-run step 6.
- You can quit Ollama from its tray icon.

## Day to day

Start Docker Desktop and Ollama, run `docker compose up -d`, then use `ingest.py` and `ask.py`, or start the API (and the web app), as needed. Run `docker compose down` when finished.
