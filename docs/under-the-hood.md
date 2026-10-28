# Under the hood: ingestion and answering

What happens inside the two workflows of the local prototype (branch `feature/rest-api`), file by file. Both run as one call chain: an entry point (a script in `scripts/`, or an HTTP endpoint in `src/rag/api/`) calls small functions in `src/rag/`. Five shared files sit underneath, and each workflow adds its own folder on top. The last section shows how the HTTP API reaches the same functions.

To run it, see [run-locally-windows.md](run-locally-windows.md). For the overall architecture, see [langchain-ollama-rag-architecture.md](langchain-ollama-rag-architecture.md).

## Shared files (both workflows and every entry point use these)

| File | What it does |
|---|---|
| `src/rag/config.py` | `get_settings()` reads `.env` into a frozen `Settings` object (model names, Ollama URL, chunk size, database URL, table name). It fills in defaults and rejects bad values early, such as an overlap bigger than the chunk size or an unsupported provider. Nothing else reads environment variables. |
| `src/rag/health.py` | `check_services()` runs before any real work. It asks Ollama's `/api/tags` whether it is up and has the models, and opens a test database connection. It returns plain-English problems; the scripts print them and exit, and the API returns them with status 503. |
| `src/rag/providers.py` | The only file that knows the backends are Ollama and pgvector. It returns a chat model (`ChatOllama`, temperature 0), embeddings (`OllamaEmbeddings`) and the vector store (`PGVectorStore`). On first use it creates the `chunks` table, sizing the vector column by embedding a test string and counting the numbers (768). `METADATA_COLUMNS` defines which metadata become real SQL columns: source, file_hash, product, version, doc_type, page, embedding_model. `unavailable_errors()` and `model_errors()` list the backend exceptions that mean "couldn't reach Ollama or the database" and "the model returned an error"; the API uses them to choose 503 or 502. |
| `src/rag/service.py` | The glue every entry point shares, so scripts and API don't each repeat it. `build_services()` creates the vector store and chat model once (a `Services` object). `run_ingest()` looks up what is already stored and runs `ingest()`. `ask()` retrieves, answers and returns the sources to show. `sources_to_show()` holds the rule "no sources when the answer is 'I don't know'". |
| `src/rag/database.py` | Plain SQL helpers that sit beside LangChain: open a connection (10 s timeout), convert the URL to the `postgresql+asyncpg://` form the vector store needs, check whether a table exists, and `indexed_files()`, which lists what is already stored. |

## Workflow 1: Ingestion (`scripts/ingest.py`)

The goal is to make the database match the `documents/` folder.

```
ingest.py
 ├─ get_settings()                               config.py
 ├─ check_services(need_chat_model=False)        health.py   (embedding model only)
 ├─ build_services(with_chat_model=False)        service.py
 │    └─ get_vector_store(get_embeddings())      providers.py
 └─ run_ingest()                                 service.py
      ├─ indexed_files()                         database.py   → what's already stored
      └─ ingest()                                ingestion/pipeline.py
           for each file:
             ├─ iter_files() / file_metadata()   ingestion/loaders.py
             ├─ unchanged? → skip
             ├─ load_file()                      ingestion/loaders.py
             ├─ split_documents()                ingestion/chunking.py
             └─ store.delete() / store.add_documents()   (embedding happens here)
           files gone from disk → store.delete()
```

### `scripts/ingest.py`: the entry point

It parses `--documents-dir`, checks the folder exists and runs the health check. It doesn't need the chat model, so ingesting works even if `gemma3:4b` isn't installed. Then it calls `build_services()` and `run_ingest()` from `service.py` and prints the report: added, updated, removed, files with no text, and timing.

### `ingestion/loaders.py`: turns files into text plus labels

- `iter_files()` walks the folder recursively. It keeps `.pdf`, `.md` and `.txt` files, skips hidden ones and sorts the rest so runs are repeatable.
- `file_metadata()` builds each file's label:
  - `source` is the path relative to `documents/`.
  - `file_hash` is a SHA-256 hash of the bytes, used to detect changes.
  - `product`, `version` and `doc_type` come from the three folder levels. A missing level becomes `None`.
- `load_file()` returns LangChain `Document`s:
  - **PDF:** one per page, read with `pypdf`, with a `page` number. Pages without extractable text (scans) are dropped.
  - **Markdown or text:** the whole file as one Document, with `page` set to `None`.

### `ingestion/chunking.py`: cuts text into bite-sized pieces

- `split_documents()` uses LangChain's `RecursiveCharacterTextSplitter`. It tries to split on paragraphs, then lines, then words, keeping chunks within 1000 characters with 150 characters of overlap. The overlap means a sentence on a boundary isn't lost.
- Each chunk inherits its page's metadata and gets a `chunk` number.

### `ingestion/pipeline.py`: decides what to do with each file

`ingest()` compares each file on disk with what `indexed_files()` returned:

| Situation | Action |
|---|---|
| Same hash and same embedding model | Skip (that's why re-runs are fast) |
| New file | Load → chunk → `store.add_documents()` |
| Changed file, or embedding model changed | Delete its old chunks, then add new ones |
| No text at all (e.g. scanned PDF) | Old chunks removed, nothing added, reported as "no text" |
| In the database but no longer on disk | Delete its chunks |

The actual embedding is hidden inside `store.add_documents()`. `PGVectorStore` sends each chunk's text to `nomic-embed-text` through Ollama, gets back 768 numbers, and inserts a row. That row holds the text, the vector, the metadata columns, and a JSON column (`langchain_metadata`) for leftover metadata such as `chunk`.

## Workflow 2: Answering (`scripts/ask.py`)

The goal is to find the most relevant passages and have the model answer only from them.

```
ask.py "question" --version 1.0
 ├─ get_settings()                          config.py
 ├─ check_services(need_chat_model=True)    health.py   (both models)
 ├─ build_services()                        service.py
 │    ├─ get_vector_store(get_embeddings()) providers.py
 │    └─ get_chat_model()                   providers.py
 ├─ retrieve()                              retrieval/search.py   → top k chunks
 ├─ stream_answer()                         generation/llm.py
 │    ├─ format_context()                   generation/prompts.py → numbered passages
 │    └─ ANSWER_PROMPT | chat model | StrOutputParser   (LCEL chain)
 ├─ sources_to_show()                       service.py   → none if "I don't know"
 └─ print sources with source_label()       generation/prompts.py
```

### `scripts/ask.py`: the entry point

It parses the question and `--version`, `--product` and `--k`, then runs the health check for both models. It gets the store and chat model from `build_services()`, prints the answer piece by piece as it streams, then lists the sources with their distance scores and the time taken. (It streams, so it calls `retrieve()` and `stream_answer()` itself; the API's non-streaming path goes through `service.ask()`, which does the same retrieve-then-answer with `answer()`.)

### `retrieval/search.py`: finds the relevant chunks

- `retrieve()` calls `store.similarity_search_with_score()`. That embeds the question with the same `nomic-embed-text` model, then runs one SQL query against the `chunks` table:
  - It filters by the `version` and `product` columns if you gave them.
  - It orders by cosine distance and takes the top `k` (default 5).
- Each result gets `metadata["distance"]`: 0 means the same meaning, and larger means less related.

### `generation/prompts.py`: what the model is told

- `ANSWER_PROMPT` is a `ChatPromptTemplate` with two messages:
  - **System:** answer only from the numbered passages, cite them like `[1]`, use no outside knowledge, and if the answer isn't there reply exactly `I don't know based on the available documents.` (the `NO_ANSWER` constant).
  - **Human:** the passages followed by the question.
- `format_context()` turns the chunks into `[1] (acme-dms/1.0/release-notes/release-notes.md, version 1.0)` followed by the text, then `[2] …`, and so on. The numbering is what lets the citations match the source list.
- `source_label()` builds that label: path, version, and page if there is one.

### `generation/llm.py`: runs the model

- `answer_chain()` is the LCEL pipeline `ANSWER_PROMPT | chat_model | StrOutputParser()`. It fills the template, sends it to `gemma3:4b`, and turns the reply message into a plain string.
- `stream_answer()`, which the CLI uses, yields text as it is generated. `answer()`, which `service.ask()` and so the API use, returns the whole text at once in an `Answer` object.
- If retrieval found nothing, both return `NO_ANSWER` without calling the model.

### Back in `ask.py`

`sources_to_show()` hides the source list when the answer contains `NO_ANSWER`. Otherwise it prints **all** retrieved passages, not just the ones the model cited.

## The same workflows over HTTP (`src/rag/api/`)

The API adds no RAG logic: each endpoint checks the services, then calls `service.py`.

```
uvicorn rag.api.app:app
 └─ create_app()                                api/app.py   (settings, CORS, error handler; nothing built yet)

GET /health
 └─ check_services(need_chat_model=True)        health.py    → 200 ok / 503 with problems

POST /chat  {"question", "product"?, "version"?, "k"?}
 ├─ ChatRequest validation                      api/schemas.py   → 422 if invalid
 ├─ check_services(need_chat_model=True)        health.py        → 503 if a problem
 ├─ get_services()                              api/app.py   → build_services() on first use, then reused
 ├─ ask()                                       service.py   → retrieve() + answer() + sources_to_show()
 └─ ChatResponse: answer, no_answer, numbered sources with excerpt

POST /documents/ingest  (no body)
 ├─ DOCUMENTS_DIR exists?                       → 500 if not
 ├─ check_services(need_chat_model=False)       health.py    → 503 if a problem
 ├─ IngestJobs.start()                          api/jobs.py  → 409 if a job is running
 ├─ background thread: run_ingest()             service.py   → jobs.succeed(report) or jobs.fail(message)
 └─ 202 with job_id

GET /documents/ingest/{job_id}
 └─ IngestJobs.get()                            api/jobs.py  → running / succeeded / failed, 404 if unknown
```

### `api/app.py`: the endpoints

- `create_app()` builds the FastAPI app. The module-level `app = create_app()` is what uvicorn serves; tests call `create_app()` with their own settings and fake services.
- Endpoints are plain `def` functions. FastAPI runs them in a thread pool, so a 30-second answer doesn't block other requests, and the vector store's synchronous calls work as in the scripts.
- `get_services()` builds the store and chat model on the first request that needs them, under a lock, and reuses them afterwards. A failed build isn't kept, so the next request tries again. This is also why the server starts, and `/health` answers, while Docker or Ollama is down.
- `_backend_failure()` turns exceptions into responses: `providers.unavailable_errors()` → 503 "Ollama or the database stopped responding", `providers.model_errors()` → 502 with Ollama's reason. Anything else becomes a generic 500; the details go only to the server log.
- `_source()` turns each passage into a `ChatSource`: its number, path, product, version, doc_type, page, distance and the first 300 characters.

### `api/schemas.py`: the request and response bodies

Pydantic models that validate input and describe output; FastAPI also publishes them at `/docs`. `ChatRequest` trims the question (1–2000 characters), caps filters at 100 characters, limits `k` to 1–20 and rejects unknown fields, so a misspelt `versoin` is an error instead of a silently ignored filter.

### `api/jobs.py`: the ingestion job registry

- `IngestJobs` holds jobs in a dictionary guarded by **one lock**. `start()` checks for a running job and registers the new one in the same locked step, so two simultaneous requests can't both start a job; the loser gets `JobAlreadyRunning` and the API returns 409 with the running job's id.
- `succeed()` and `fail()` record the result and free the slot. `get()` returns an immutable snapshot. Only the 50 most recent finished jobs are kept.
- It lives **in memory**: history disappears when the server restarts, and each uvicorn worker process would have its own. Run a single worker; a shared deployment would move jobs to a database table or a queue.

## Things worth knowing

- **Same embedding model on both sides.** Questions and chunks must be embedded by the same model, or the distances are meaningless. That's why `embedding_model` is stored on every row, and why changing it makes ingest redo every file.
- **LangChain stays inside `src/rag/`.** The scripts and the API only call our own functions (`build_services()`, `run_ingest()`, `ask()`, `retrieve()`, `stream_answer()`), and the tests swap in fake stores and models.
- **No relevance cut-off.** You always get the top `k` chunks, even if they are weakly related. The prompt is the only guard against made-up answers.
- **Two database clients.** `PGVectorStore` uses asyncpg; `database.py` uses psycopg for the small direct queries the vector store doesn't offer, such as "which files are stored, with which hash".
