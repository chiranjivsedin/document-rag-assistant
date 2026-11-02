# Under the hood: ingestion, answering and the document library

What happens inside the workflows of the local prototype (branch `integration`), file by file. Each runs as one call chain: an entry point (a script in `scripts/`, or an HTTP endpoint in `src/rag/api/`) calls small functions in `src/rag/`. Five shared files sit underneath, and each workflow adds its own folder on top. After ingestion and answering, a section shows how the HTTP API reaches the same functions, and the last workflow is the document library (`src/rag/dms/`), which only the API uses. The web app in `frontend/` only calls the API, so it adds no steps of its own.

To run it, see [run-locally-windows.md](run-locally-windows.md). For the overall architecture, see [langchain-ollama-rag-architecture.md](langchain-ollama-rag-architecture.md).

## Shared files (both workflows and every entry point use these)

| File | What it does |
|---|---|
| `src/rag/config.py` | `get_settings()` reads `.env` into a frozen `Settings` object (model names, Ollama URL and keep-alive time, chunk size, database URL, table name, upload size limit). It fills in defaults and rejects bad values early, such as an overlap bigger than the chunk size or an unsupported provider. Nothing else reads environment variables. |
| `src/rag/health.py` | `check_services()` runs before any real work. It asks Ollama's `/api/tags` whether it is up and has the models, and opens a test database connection. It returns plain-English problems; the scripts print them and exit, and the API returns them with status 503. |
| `src/rag/providers.py` | The only file that knows the backends are Ollama and pgvector. It returns a chat model (`ChatOllama`, temperature 0), embeddings (`OllamaEmbeddings`) and the vector store (`PGVectorStore`). Both models send a keep-alive time with every call (`OLLAMA_KEEP_ALIVE_MINUTES`, default 30), so Ollama keeps them loaded between questions instead of unloading them after its default 5 idle minutes; reloading the chat model costs 15–30 s. On first use it creates the `chunks` table, sizing the vector column by embedding a test string and counting the numbers (768). `METADATA_COLUMNS` defines which metadata become real SQL columns: source, file_hash, product, version, doc_type, page, embedding_model. `unavailable_errors()` and `model_errors()` list the backend exceptions that mean "couldn't reach Ollama or the database" and "the model returned an error"; the API uses them to choose 503 or 502. |
| `src/rag/service.py` | The glue every entry point shares, so scripts and API don't each repeat it. `build_services()` creates the vector store and chat model once (a `Services` object). `run_ingest()` looks up what is already stored and runs `ingest()`. `ask()` retrieves, answers and returns the sources to show. `sources_to_show()` holds the rule "no sources when the answer is 'I don't know'". |
| `src/rag/database.py` | Plain SQL helpers that sit beside LangChain: open a connection (10 s timeout), convert the URL to the `postgresql+asyncpg://` form the vector store needs, check whether a table exists, and `indexed_files()`, which lists what is already stored. For the document library it also has `chunk_counts()` (passages per file) and `delete_chunks()` (one file's passages), which work without Ollama. |

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
 ├─ has_no_words() / only_says_not_stated() generation/citations.py → "I don't know" if not a real answer
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
  - **System:** answer only from the numbered passages, as a short list of rules. Each rule answers a failure seen with `gemma3:4b`:
    - answer in sentences that state the facts in words; a citation alone is never an answer (it once replied just `[1]`);
    - only state what the passages say, with no guessing or outside knowledge;
    - when passages cover different versions, say which version each fact applies to;
    - end each sentence with its citation, following an example sentence, and cite only the passages that state that fact, each once (it used to stack `[1][2]` when `[2]` said nothing about the fact);
    - if the answer isn't there, reply exactly `I don't know based on the available documents.` (the `NO_ANSWER` constant), with no citation.
  - **Human:** the passages followed by the question.
- `format_context()` turns the chunks into `[1] (acme-dms/1.0/release-notes/release-notes.md, version 1.0)` followed by the text, then `[2] …`, and so on. The numbering is what lets the citations match the source list.
- `source_label()` builds that label: path, version, and page if there is one.

### `generation/llm.py`: runs the model

- `answer_chain()` is the LCEL pipeline `ANSWER_PROMPT | chat_model | StrOutputParser()`. It fills the template, sends it to `gemma3:4b`, and turns the reply message into a plain string.
- `stream_answer()`, which the CLI uses, yields text as it is generated. `answer()`, which `service.ask()` and so the API use, returns the whole text at once in an `Answer` object, after passing it through the checks in `citations.py` (below).
- If retrieval found nothing, both return `NO_ANSWER` without calling the model.

### `generation/citations.py`: checks on the reply

Small, model-independent checks, so a quirk of the model doesn't reach the user:

- `tidy_citations()` merges a number repeated in one place: `[1][1]`, `[1] [1]` and `[1, 1]` become `[1]`. It never changes the words, and it leaves ranges such as `[1-3]` as written.
- `has_no_words()` is true for a reply that is empty or only citations and punctuation, such as `[1]`.
- `only_says_not_stated()` is true when every sentence only says the documents lack the answer, e.g. "The release date of 2.0 is not stated in the passages [1]." It needs both a phrase such as "is not stated / mentioned / specified" or "no information" and a mention of the passages or documents, so a product fact like "is not supported in 2.0" still counts as an answer. English wording only.

`answer()` applies `tidy_citations()`, then replaces the reply with `NO_ANSWER` if either of the other two checks is true. That keeps `no_answer` in `/chat`, and the hidden source list, working when the model words "I don't know" its own way.

### Back in `ask.py`

Because the answer was printed as it streamed, `ask.py` can't tidy citations, but it applies the other two checks: if the reply isn't a real answer, it prints `NO_ANSWER` and treats the answer as that. `sources_to_show()` then hides the source list when the answer contains `NO_ANSWER`. Otherwise it prints **all** retrieved passages, not just the ones the model cited.

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
 ├─ ask()                                       service.py   → retrieve() + answer() (with citations.py checks) + sources_to_show()
 └─ ChatResponse: answer, no_answer, numbered sources with excerpt

POST /documents/ingest  (no body)
 ├─ DOCUMENTS_DIR exists?                       → 500 if not
 ├─ check_services(need_chat_model=False)       health.py    → 503 if a problem
 ├─ start_job() → IngestJobs.start()            api/app.py, api/jobs.py  → 409 if a job is running
 ├─ background thread: tracked_ingest()         api/app.py
 │   ├─ library.tracker.run(run_ingest)         dms/tracking.py + service.py  (plain run_ingest() if the library is unavailable)
 │   ├─ jobs.succeed(report) or jobs.fail(message)
 │   └─ after success: documents still pending? → start another job
 └─ 202 with job_id

GET /documents/ingest/{job_id}
 └─ IngestJobs.get()                            api/jobs.py  → running / succeeded / failed, 404 if unknown
```

### `api/app.py`: the endpoints

- `create_app()` builds the FastAPI app. The module-level `app = create_app()` is what uvicorn serves; tests call `create_app()` with their own settings and fake services.
- Endpoints are plain `def` functions. FastAPI runs them in a thread pool, so a 30-second answer doesn't block other requests, and the vector store's synchronous calls work as in the scripts.
- `get_services()` builds the store and chat model on the first request that needs them, under a lock, and reuses them afterwards. A failed build isn't kept, so the next request tries again. This is also why the server starts, and `/health` answers, while Docker or Ollama is down. `get_library()` does the same for the document library.
- `start_job()` registers a job and starts its thread; both `POST /documents/ingest` and an upload use it, so there is one job runner. `start_indexing_if_idle()` is the upload's version: it starts a job only if none is running and the services are up, and never raises. `tracked_ingest()` runs the ingestion inside the library's `IndexingTracker` so document statuses follow the run.
- `backend_failure()` (in `api/errors.py`) turns exceptions into responses: `providers.unavailable_errors()` → 503 "Ollama or the database stopped responding", `providers.model_errors()` → 502 with Ollama's reason. Anything else becomes a generic 500; the details go only to the server log.
- `_source()` turns each passage into a `ChatSource`: its number, path, product, version, doc_type, page, distance and the whole passage text as `excerpt`.

### `api/schemas.py`: the request and response bodies

Pydantic models that validate input and describe output; FastAPI also publishes them at `/docs`. `ChatRequest` trims the question (1–2000 characters), caps filters at 100 characters, limits `k` to 1–20 and rejects unknown fields, so a misspelt `versoin` is an error instead of a silently ignored filter.

### `api/jobs.py`: the ingestion job registry

- `IngestJobs` holds jobs in a dictionary guarded by **one lock**. `start()` checks for a running job and registers the new one in the same locked step, so two simultaneous requests can't both start a job; the loser gets `JobAlreadyRunning` and the API returns 409 with the running job's id.
- `succeed()` and `fail()` record the result and free the slot. `get()` returns an immutable snapshot, and `running()` the job running now, if any. Only the 50 most recent finished jobs are kept.
- It lives **in memory**: history disappears when the server restarts, and each uvicorn worker process would have its own. Run exactly one worker, and stop the old process completely before starting a new one: the document library's status tracking relies on this too. A shared deployment would move jobs to a database table or a queue.

## Workflow 3: The document library (`src/rag/dms/`)

Upload files into the documents folder, follow their indexing, serve and delete the originals. Only the API uses it (`src/rag/api/documents.py`); the scripts don't. It never runs the pipeline itself: it records what an upload changed, and the ingestion job runner (above) indexes it.

```
POST /documents  (multipart: file, product, version, doc_type)
 ├─ UploadSizeLimit                              api/documents.py → 413 over MAX_UPLOAD_MB (from Content-Length, or counted as it arrives)
 ├─ library()                                    api/documents.py → 503 without DATABASE_URL, 500 without DOCUMENTS_DIR; build_library() on first use
 ├─ DocumentLibrary.upload()                     dms/library.py
 │   ├─ check_folder_name() ×3, clean_filename() dms/storage.py   → 422 if not acceptable
 │   ├─ source_for()                             dms/storage.py   → <product>/<version>/<doc_type>/<name>, existing capitals reused
 │   ├─ mark the path as uploading; taken?       → 409
 │   ├─ DocumentStorage.save()                   dms/storage.py   → .uploads/ temp file, size + SHA-256, check_content(), link into place
 │   └─ DocumentRepository.add()                 dms/repository.py → status pending (the file is deleted again if this fails)
 ├─ start_indexing_if_idle()                     api/app.py       → a job, or none (job running, or services down)
 └─ 201 with the document and ingest_job_id

GET /documents, GET /documents/{id}             → DocumentRepository.list_all() / get()
GET /documents/{id}/file[?download=true]        → readable_file() (no links, one hard link) → FileResponse
DELETE /documents/{id}                          → 409 if a job runs; else file → delete_chunks() → record

An ingestion job (any trigger): IndexingTracker.run()   dms/tracking.py
 ├─ release_processing()                         back to pending: left over by a stopped server
 ├─ claim_for_indexing()                         pending + failed → processing
 ├─ run_ingest()                                 service.py (the unchanged pipeline)
 └─ from the report: claimed → indexed / failed; changed or vanished files; adopt files not yet recorded
```

### `dms/repository.py`: the documents table

- `DocumentRepository` reads and writes the `documents` table with psycopg, one short connection per call, like `database.py`. `create_table()` creates it on first use; check constraints repeat the main rules, so the database refuses a bad row too.
- `NewDocument` is a file about to be recorded. It checks the path is a plain relative path and that product, version and type match its folders exactly as the loaders read them, so a record and its chunks always agree.
- `add()` records an upload as `pending`, and raises `DuplicateDocument` if the path is taken. `adopt()` records a file found in the folder (`origin: folder`) as indexed, pending or failed, and does nothing if the path is already recorded, so it never takes over an upload.
- Status changes are scoped: `claim_for_indexing()` turns `pending` and `failed` rows into `processing`, and `mark_indexed()` / `mark_failed()` only change rows that are `processing`. A run therefore never changes a document it didn't claim. `refresh_indexed()` and `mark_missing()` only touch `indexed` rows; `release_processing()` puts `processing` rows back to `pending`.

### `dms/storage.py`: the files

- `check_folder_name()` checks product, version and type: trimmed, 1–64 letters, digits, `.`, `_` or `-`, letters or digits at both ends, no `..`, no Windows device names. `clean_filename()` keeps the last part of the uploaded name, reduces it to safe ASCII characters (at most 120), and refuses any type but `.pdf`, `.md` and `.txt`. The web app repeats these rules in `frontend/src/features/library/validation.ts`; change both together.
- `check_content()` refuses a PDF that doesn't start like one or that the loader can't read, text that isn't UTF-8 or contains NUL bytes; `save()` refuses an empty file.
- `save()` streams the upload into the hidden `.uploads/` folder (counting the size and computing the SHA-256 as it goes), checks it, then puts it in place with `os.link`, which fails if a file appeared at that path, so an existing document is never overwritten. The temporary file is always removed.
- `path_of()` is how every stored path is turned into a file: it checks the path itself (relative, no `..`, no drive or `:`, no hidden parts, no control characters, no trailing dot or space, no device names) and refuses any symlink or junction on the way, without resolving paths, which gave inconsistent results under concurrent uploads on Windows. `save()` checks again just before writing, and refuses a linked `.uploads/` folder. `readable_file()` also refuses a file with more than one hard link.

### `dms/library.py`: uploads, lookups and deletes

- `DocumentLibrary.upload()` checks the names, works out the path, and keeps it in an "uploading" set until the record exists, so a concurrent ingestion run never records the new file as found in the folder (which would turn the upload into a duplicate). If recording fails, it deletes the file it just saved.
- `delete()` removes the file, then its chunks (through `IndexAccess`), then the record. If a step fails it raises `DeletionFailed` with what was done; every step is safe to repeat, so deleting again finishes the job.
- `build_library()` creates the table, puts documents left `processing` back to `pending`, and adopts files already in the folder. The API calls it on first use, once per server process, before that process's first tracked run, which is why it can't reset a running job when only one process runs.

### `dms/tracking.py`: indexing status around each run

- `IndexingTracker.run()` wraps one ingestion run: release, claim, run the unchanged pipeline, then record the outcome from its `IngestReport`. Claimed files in `added` / `updated` / `unchanged` become indexed with their chunk count (`chunk_counts()`); a file in `no_text` becomes failed ("No text could be extracted…"); a claimed file the run didn't see becomes failed ("The original file is no longer in the documents folder."). For documents it didn't claim, `updated` refreshes an indexed record and `removed` marks it failed.
- If the run raises, every claimed document is marked failed with the job's client-safe message, and the error is passed on. If even that can't be recorded, or recording a successful run fails half-way, the claims go back to `pending`, or the next run's release step frees them.
- `adopt_untracked()` scans the folder for files without a record; `run()` also adopts files the report saw. Both skip files mid-upload and files `readable_file()` refuses.

### `dms/index.py` and `api/documents.py`

- `IndexAccess` bundles the three things the library needs from the `chunks` table (delete one file's chunks, count chunks per file, list indexed files) and the current embedding model. In the app they are `database.py` helpers; in tests, fakes.
- `api/documents.py` holds the `/documents` endpoints and `UploadSizeLimit`, an ASGI middleware that caps the upload body: it refuses an oversized `Content-Length` before reading, and counts the bytes of every upload as they arrive, stopping with 413 once past the limit. This is needed because the form parser saves uploaded files to a temporary file, with no limit of its own, before the endpoint runs. Unsafe paths become a clear 500; a delete that stopped part-way returns what was done.

## Things worth knowing

- **Same embedding model on both sides.** Questions and chunks must be embedded by the same model, or the distances are meaningless. That's why `embedding_model` is stored on every row, and why changing it makes ingest redo every file.
- **LangChain stays inside `src/rag/`.** The scripts and the API only call our own functions (`build_services()`, `run_ingest()`, `ask()`, `retrieve()`, `stream_answer()`), and the tests swap in fake stores and models.
- **No relevance cut-off.** You always get the top `k` chunks, even if they are weakly related. The prompt is the only guard against made-up answers; the checks in `citations.py` only catch replies that aren't answers, not wrong ones.
- **Two database clients.** `PGVectorStore` uses asyncpg; `database.py` and the document library use psycopg for the small direct queries the vector store doesn't offer, such as "which files are stored, with which hash".
- **The library and the index meet only at `source`.** A `documents` row and a file's chunks share the same `source` path; there is no foreign key, and the library changes the `chunks` table only to delete one document's chunks.
