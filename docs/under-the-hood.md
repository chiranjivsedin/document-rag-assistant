# Under the hood: ingestion and answering

What happens inside the two workflows of the local prototype (branch `poc/langchain-ollama`), file by file. Both run as one call chain: a script in `scripts/` calls small functions in `src/rag/`. Four shared files sit underneath, and each workflow adds its own folder on top.

To run it, see [run-locally-windows.md](run-locally-windows.md). For the overall architecture, see [langchain-ollama-rag-architecture.md](langchain-ollama-rag-architecture.md).

## Shared files (both workflows use these)

| File | What it does |
|---|---|
| `src/rag/config.py` | `get_settings()` reads `.env` into a frozen `Settings` object (model names, Ollama URL, chunk size, database URL, table name). It fills in defaults and rejects bad values early, such as an overlap bigger than the chunk size or an unsupported provider. Nothing else reads environment variables. |
| `src/rag/health.py` | `check_services()` runs before any real work. It asks Ollama's `/api/tags` whether it is up and has the models, and opens a test database connection. It returns plain-English problems; the scripts print them and exit. |
| `src/rag/providers.py` | The only file that knows the backends are Ollama and pgvector. It returns a chat model (`ChatOllama`, temperature 0), embeddings (`OllamaEmbeddings`) and the vector store (`PGVectorStore`). On first use it creates the `chunks` table, sizing the vector column by embedding a test string and counting the numbers (768). `METADATA_COLUMNS` defines which metadata become real SQL columns: source, file_hash, product, version, doc_type, page, embedding_model. |
| `src/rag/database.py` | Plain SQL helpers that sit beside LangChain: open a connection (10 s timeout), convert the URL to the `postgresql+asyncpg://` form the vector store needs, check whether a table exists, and `indexed_files()`, which lists what is already stored. |

## Workflow 1: Ingestion (`scripts/ingest.py`)

The goal is to make the database match the `documents/` folder.

```
ingest.py
 ├─ get_settings()                          config.py
 ├─ check_services(need_chat_model=False)   health.py   (embedding model only)
 ├─ get_vector_store(get_embeddings())      providers.py
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

It parses `--documents-dir`, checks the folder exists and runs the health check. It doesn't need the chat model, so ingesting works even if `gemma3:4b` isn't installed. Then it wires the pieces together and prints the report: added, updated, removed, files with no text, and timing.

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
 ├─ get_vector_store(get_embeddings())      providers.py
 ├─ retrieve()                              retrieval/search.py   → top k chunks
 ├─ stream_answer()                         generation/llm.py
 │    ├─ format_context()                   generation/prompts.py → numbered passages
 │    └─ ANSWER_PROMPT | chat model | StrOutputParser   (LCEL chain)
 └─ print sources with source_label()       generation/prompts.py
```

### `scripts/ask.py`: the entry point

It parses the question and `--version`, `--product` and `--k`, then runs the health check for both models. It prints the answer piece by piece as it streams, then lists the sources with their distance scores and the time taken.

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
- `stream_answer()`, which the CLI uses, yields text as it is generated. `answer()` returns the whole text at once in an `Answer` object.
- If retrieval found nothing, both return `NO_ANSWER` without calling the model.

### Back in `ask.py`

If the answer contains `NO_ANSWER`, the source list is hidden. Otherwise it prints **all** retrieved passages, not just the ones the model cited.

## Things worth knowing

- **Same embedding model on both sides.** Questions and chunks must be embedded by the same model, or the distances are meaningless. That's why `embedding_model` is stored on every row, and why changing it makes ingest redo every file.
- **LangChain stays inside `src/rag/`.** The scripts only call `ingest()`, `retrieve()` and `stream_answer()`. A future web API could call the same functions, and the tests swap in fake stores and models.
- **No relevance cut-off.** You always get the top `k` chunks, even if they are weakly related. The prompt is the only guard against made-up answers.
- **Two database clients.** `PGVectorStore` uses asyncpg; `database.py` uses psycopg for the small direct queries the vector store doesn't offer, such as "which files are stored, with which hash".
