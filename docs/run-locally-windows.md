# Running the prototype manually on Windows

A step-by-step guide to running the local RAG prototype (branch `poc/langchain-ollama`) by hand, with what each command does. For how the pieces fit together, see [langchain-ollama-rag-architecture.md](langchain-ollama-rag-architecture.md).

Commands are written for **PowerShell**. Steps 1–4 are one-time setup; after that you only need steps 5–9.

## Before you start: three programs

| Program | Why | Check it with |
|---|---|---|
| **Python 3.10+** | Runs the app | `python --version` |
| **Docker Desktop** | Runs the PostgreSQL database | Docker Desktop open, whale icon in the system tray |
| **Ollama** | Runs the AI models on your laptop | `ollama --version` |

## 1. Go to the project and the right branch

```powershell
cd <path-to-your-clone>\RAG
git switch poc/langchain-ollama
```

- `cd` moves into the project folder. Every later command assumes you are here.
- `git switch` selects the branch with the working prototype. `main` only has the empty scaffold.

## 2. Create the Python environment (one-time)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[ollama,pgvector,dev]"
```

- `python -m venv .venv` creates a private Python environment in the `.venv` folder, so this project's packages don't mix with other projects. Skip it if `.venv` already exists.
- `pip install -e ".[ollama,pgvector,dev]"` installs the app and its libraries:
  - `-e` means "editable": your code changes take effect without reinstalling.
  - `[ollama,pgvector,dev]` adds the optional groups: the Ollama connection, the PostgreSQL vector store, and pytest for tests.

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

**Memory:** the chat model needs about 4 GB of free RAM. If you see "model requires more system memory", close some apps or restart Ollama.

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
- **Time:** expect 15–40 seconds per answer on a laptop CPU, and up to a minute for the first one while the model loads.
- **Unanswerable questions:** if the documents don't contain the answer, it replies `I don't know based on the available documents.`

## 8. If something isn't running

Both scripts check the services first and stop with one clear line instead of a long error:

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

## 9. Run the tests (optional)

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:warnings
.\.venv\Scripts\python.exe -m pytest -m integration -p no:warnings
```

- The first runs the **unit tests**. They use stand-ins, so Docker and Ollama aren't needed, and they take a few seconds.
- The second runs the **integration tests** against the real database and Ollama, in about 30–60 seconds. They use their own temporary tables, so your indexed data isn't touched.
- `-p no:warnings` hides a harmless warning from a LangChain dependency on Python 3.14.
- To run one test: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_chunking.py::<test_name>`.

## 10. When you're done

```powershell
docker compose down
```

- This stops the database and frees memory. Your indexed data is kept.
- `docker compose down -v` would delete the data too, and you'd need to re-run step 6.
- You can quit Ollama from its tray icon.

## Day to day

Start Docker Desktop and Ollama, run `docker compose up -d`, then use `ingest.py` and `ask.py` as needed. Run `docker compose down` when finished.
