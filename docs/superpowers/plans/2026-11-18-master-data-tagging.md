# Master-Data Tagging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every document is tagged only with values from managed master lists (Product, Version, Document type, plus admin-defined extra categories), usable in Upload, Documents and Chat, with an admin screen protected by an admin key.

**Architecture:** New tables `tag_categories`, `tag_values`, `document_tags` behind a `TagRepository` in `src/rag/dms/tags.py`. The three built-in categories validate the existing `product`/`version`/`doc_type` fields (still folder names, still chunk columns); extra categories live only in `document_tags`. Chat filters extra tags by turning them into an allowed set of `source` paths that both the vector search and the keyword search apply in SQL. No re-indexing.

**Tech Stack:** Python 3.14, FastAPI, psycopg 3, PostgreSQL 18 + pgvector, langchain-postgres `PGVectorStore` v2; React + TypeScript + Vite, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-18-master-data-tagging-design.md` (read it first; it decides the what and the why).

## Global Constraints

- Branch `feature/master-data-tagging` only (it holds the spec commit `75044ed`). Commit after each task; **never push, merge or touch other branches**.
- Read `CLAUDE.md` first and follow its conventions: src-layout imports `from rag...`; endpoints plain `def`; services and the library built on first use; shared glue in `service.py`; LangChain only behind our functions; one uvicorn worker; frontend name rules mirrored in `validation.ts`.
- Built-in category keys are exactly `product`, `version`, `doc_type`; labels "Product", "Version", "Document type".
- Seed (only when `tag_categories` is empty), codes exactly:
  - product: `content-management` → "Content Management"
  - version (labels = codes): `16.2 16.4 16.6 16.7 20.2 20.3 20.4 21.1 21.2 21.3 21.4 22.1 22.2 22.3 22.4 23.1 23.2 23.3 23.4 24.1 24.2 24.3 24.4 25.1 25.2 25.3 25.4 26.1 26.2 26.4` (30; no 26.3)
  - doc_type: `release-notes` "Release notes", `installation-guide` "Installation guide", `upgrade-guide` "Upgrade guide", `admin-guide` "Administration guide"
- Built-in codes must pass `rag.dms.storage.check_folder_name()`. Codes are never renamed; only labels change.
- Values are retired (`active = false`), never deleted while used (by a document row, a `document_tags` row, or a chunk's product/version/doc_type).
- Setting `ADMIN_KEY` (default empty). Empty → admin endpoints answer **403** "Admin is disabled". Wrong/missing header **`X-Admin-Key`** → **401**. Compare with `hmac.compare_digest`. The key never appears in logs, URLs, error messages or the question log.
- `PUT /documents/{id}/tags` is **not** admin-protected.
- The repository is public: seeds and tests use only the values above or invented ones (e.g. `acme`), never client document text.
- Don't touch `documents/`, `.env`, `CLAUDE.local.md`, `docs/deployment-azure-vm.md`, `docs/approval-request-azure-vm.md`, `deploy-kit/`.
- Unit tests: `.venv/Scripts/python.exe -m pytest -q -p no:warnings` (663 pass at the start; all must keep passing). Integration tests need only the Docker database: `docker compose up -d` (start Docker Desktop if needed), then `.venv/Scripts/python.exe -m pytest -m integration -q -p no:warnings tests/integration/test_tags_live.py` (plus any integration file a task changes). **Don't start Ollama**; stop the containers you started at the end (`docker compose stop`). Frontend: `cd frontend && npm run typecheck && npm test && npm run build`.

## Review Focus

1. **Case and spacing duplicates:** adding value label "Release Notes" when "Release notes" exists, or code "Release-Notes" next to "release-notes", must be refused (409), compared case-insensitively after trimming. → test in Task 1 (`test_duplicate_label_or_code_differing_only_in_case_is_refused`).
2. **Version order:** `16.10` sorts after `16.9`, `20.2` after `16.7`, non-numeric codes (e.g. imported `latest`) after all numeric ones. → Task 1 (`test_versions_sort_numerically`).
3. **Re-start on an existing database** (the VM, the laptop): seeding runs again without duplicates and doesn't undo admin changes (a retired or relabelled seed value stays as the admin left it). → Task 1 (`test_seed_is_idempotent_and_keeps_admin_changes`).
4. **Tag filter that matches no document**, or only a retired value: chat returns the fixed "I don't know" reply without calling the model; a retired value still filters. → Task 4 (`test_tag_filter_with_no_matching_document_answers_without_the_model`, `test_retired_value_still_filters`).
5. **Admin key handling:** header with surrounding spaces, empty header, key sent as a query parameter (`?key=`) must all fail (401), and a 401/403 response body never echoes the key. → Task 2 (`test_admin_key_variants_are_refused`).

---

### Task 1: Tag tables, seed and repository

**Files:**
- Create: `src/rag/dms/tags.py`
- Test: `tests/unit/test_tags.py` (pure functions), `tests/integration/test_tags_live.py` (real database, own temporary table names like the other `*_live.py` files)

**Interfaces:**
- Produces (in `rag.dms.tags`):
  - `BUILTIN_KEYS = ("product", "version", "doc_type")`
  - `@dataclass(frozen=True) TagValue(id: int, category_key: str, code: str, label: str, active: bool, sort_order: int)`
  - `@dataclass(frozen=True) TagCategory(key: str, label: str, builtin: bool, multiple: bool, values: list[TagValue])`
  - `version_sort_key(code: str) -> tuple` (numeric parts compared as numbers; non-numeric codes after numeric ones)
  - exceptions `TagNotFound(Exception)`, `TagConflict(Exception)` (message is client-safe), `TagInUse(Exception)`
  - `class TagRepository(database_url: str, documents_table: str = "documents", prefix: str = "")` (prefix is prepended to the three table names so tests use their own tables) with:
    - `create_tables() -> None` (idempotent; `document_tags.document_id` references the documents table `ON DELETE CASCADE`; `tag_values` unique on `(category_id, lower(code))`, and on `(category_id, lower(label))` where active)
    - `seed() -> None` (insert the Global-Constraints values only if `tag_categories` is empty; versions get `sort_order` from `version_sort_key`). Existing documents' values are **not** imported: they are test samples, shown as "not in master list".
    - `categories() -> list[TagCategory]` (categories by `sort_order`, built-ins first; values sorted by `sort_order`, then label)
    - `add_category(key: str, label: str, multiple: bool) -> TagCategory` (key: lowercase letters, digits, hyphens, ≤ 32 chars, not a built-in key; `TagConflict` if taken)
    - `add_value(category_key: str, code: str, label: str) -> TagValue` (built-in codes checked with `check_folder_name`; extra-category codes: same rule; `TagConflict` on case-insensitive duplicate code or active label)
    - `update_value(value_id: int, label: str | None, active: bool | None, sort_order: int | None) -> TagValue`
    - `delete_value(value_id: int) -> None` (`TagInUse` if any use per Global Constraints)
    - `is_active_code(category_key: str, code: str) -> bool`
    - `document_tags(document_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[TagValue]]`
    - `set_document_tags(document_id: uuid.UUID, value_ids: Sequence[int]) -> list[TagValue]` (only active values of extra categories; at most one per single-value category; replaces all extra tags; `TagNotFound` / `TagConflict`)
    - `sources_with_tags(selected: Mapping[str, Sequence[str]]) -> set[str]` (documents.source of documents that, for every given extra category key, have at least one of the given codes; retired values count)

- [ ] **Step 1:** Write unit tests in `tests/unit/test_tags.py`: `test_versions_sort_numerically` (`sorted(["16.10", "latest", "20.2", "16.9", "16.2"], key=version_sort_key) == ["16.2", "16.9", "16.10", "20.2", "latest"]`), and key/code validation helpers if you factor them out.
- [ ] **Step 2:** Write integration tests in `tests/integration/test_tags_live.py`: `test_seed_creates_the_three_builtins` (product has `content-management`; version has exactly the 30 codes in order; doc_type the 4 codes with their labels), `test_seed_is_idempotent_and_keeps_admin_changes` (seed, retire `16.2` and relabel `release-notes`, seed again: no duplicates, changes kept), `test_existing_document_values_are_not_imported` (a documents row with product `acme` stays out of the product list), `test_duplicate_label_or_code_differing_only_in_case_is_refused`, `test_retired_value_is_kept_but_inactive`, `test_value_in_use_cannot_be_deleted` (used by a document row; and an unused one can), `test_set_document_tags_rules` (built-in value refused, retired refused, two values in a single-value category refused, multi-value category accepts two), `test_document_delete_cascades_its_tags`, `test_sources_with_tags_requires_every_category` (two categories → intersection; within one → union).
- [ ] **Step 3:** Run both; expect failures (module missing).
- [ ] **Step 4:** Implement `src/rag/dms/tags.py` (psycopg with `sql.Identifier` for table names, parameters for every value; use the existing helpers in `rag.database` for connections).
- [ ] **Step 5:** Run unit + `pytest -m integration tests/integration/test_tags_live.py`; all pass; full unit suite still passes.
- [ ] **Step 6:** Commit `feat(tags): master-data tables, seed and repository`.

### Task 2: Admin key setting and the `/tags` API

**Files:**
- Modify: `src/rag/config.py` (`admin_key: str | None` from `ADMIN_KEY`, stripped, empty → None), `.env.example` (documented, empty)
- Create: `src/rag/api/tags.py` (router), Modify: `src/rag/api/app.py` (lazy `get_tags()` like `get_library()`, built with `TagRepository(...).create_tables(); seed()`; include router), `src/rag/api/schemas.py`
- Test: `tests/unit/test_tags_api.py` (fake repository), `tests/unit/test_config.py`

**Interfaces:**
- Consumes: Task 1 `TagRepository`, `TagCategory`, `TagValue`, exceptions.
- Produces: `create_app(settings, services=None, library=None, tags=None)` (a prebuilt/fake repository for tests); `GET /tags` → `{"categories": [{"key","label","builtin","multiple","values":[{"id","code","label","active"}]}]}`; admin endpoints exactly as the spec's API section; `require_admin(settings, x_admin_key: str | None) -> None` raising `HTTPException(403|401)`.

- [ ] **Step 1:** Tests: `test_get_tags_is_open`, `test_admin_disabled_without_admin_key` (403 for each admin endpoint), `test_admin_key_variants_are_refused` (missing header, empty, `" <key> "` with spaces, `?key=<key>` query param → 401; body never contains the key), `test_admin_endpoints_with_the_right_key` (201/200 and the repository called with the given values), `test_conflict_is_409_and_in_use_is_409`, `test_not_found_is_404`, `test_admin_key_setting` in test_config.
- [ ] **Step 2:** Run; expect failures. **Step 3:** Implement. **Step 4:** Run unit suite; pass.
- [ ] **Step 5:** Commit `feat(api): tags endpoints with admin key`.

### Task 3: Upload validation, document tags and Documents filters (API)

**Files:**
- Modify: `src/rag/dms/library.py` (`upload(...)` gains `extra_tags: Sequence[int] = ()` and a `tags` collaborator; checks `is_active_code` for product/version/doc_type before storing; applies extra tags after the record exists; `build_library(settings, ...)` builds/uses the `TagRepository`), `src/rag/api/documents.py`, `src/rag/api/schemas.py`
- Test: `tests/unit/test_documents_api.py`, `tests/unit/test_dms_library.py` (or the existing library test file), `tests/integration/test_documents_api_live.py` (one live case)

**Interfaces:**
- Consumes: Task 1 (`is_active_code`, `set_document_tags`, `document_tags`), Task 2 (`get_tags`).
- Produces: `POST /documents` form fields unchanged plus optional repeated `tag` (value ids); 422 with "Version '9.9' is not in the master list" style messages (built-in label + code); `DocumentResponse.tags: list[{"id","category","code","label","active"}]` and `DocumentResponse.unlisted: list[str]` (built-in category keys whose value isn't an active-or-retired code); `PUT /documents/{id}/tags` body `{"value_ids": [int]}` → the document; `GET /documents?tag=<category>:<code>` (repeatable; same all-categories/any-value rule as chat; built-in categories filter their columns).

- [ ] **Step 1:** Tests: `test_upload_refuses_a_code_not_in_the_list`, `test_upload_refuses_a_retired_code`, `test_upload_with_extra_tags`, `test_put_tags_replaces_extra_tags`, `test_put_tags_refuses_builtin_or_retired`, `test_list_includes_tags_and_unlisted`, `test_list_filters_by_tag`, and one live test (upload with a tag, list, filter).
- [ ] **Step 2:** Run; fail. **Step 3:** Implement. **Step 4:** Run unit + the changed integration file; pass.
- [ ] **Step 5:** Commit `feat(dms): master-data validation and document tags`.

### Task 4: Tag filters in chat (retrieval)

**Files:**
- Modify: `src/rag/retrieval/search.py` (`retrieve(..., doc_type: str | None = None, sources: AbstractSet[str] | None = None, ...)`), `src/rag/retrieval/keyword.py` (`search_query` / `keyword_search` accept a `sources` set → `c.source = ANY(%(sources)s)`), `src/rag/service.py` (`ask`/`find_passages` gain `doc_type` and `tags: Mapping[str, Sequence[str]] | None`; resolve tags to sources via the tag repository passed in `Services` or as an argument; empty set → `NO_ANSWER` with no model call), `src/rag/api/schemas.py` (`ChatRequest.doc_type: Filter | None`, `tags: dict[str, list[str]] | None`), `src/rag/api/app.py`, `src/rag/question_log.py` (entry gains `doc_type` and `tags`)
- Test: `tests/unit/test_search.py`, `tests/unit/test_keyword.py`, `tests/unit/test_service.py`, `tests/unit/test_api.py`, `tests/unit/test_question_log.py`, `tests/integration/test_keyword_search_live.py` (one case with `sources`)

**Interfaces:**
- Consumes: Task 1 `sources_with_tags`.
- Produces: vector search filter for sources uses the store's `$in` operator on the `source` metadata column (`{"source": {"$in": sorted(sources)}}`, combined with the equality filters under `$and` when both exist; check the installed langchain-postgres filter syntax); keyword SQL uses a parameter, never string formatting.

- [ ] **Step 1:** Tests: `test_doc_type_filter_reaches_both_searches`, `test_sources_filter_reaches_both_searches`, `test_tag_filter_with_no_matching_document_answers_without_the_model`, `test_retired_value_still_filters`, `test_chat_rejects_unknown_tag_category` (422), `test_question_log_records_doc_type_and_tags`, keyword SQL test for `ANY(%(sources)s)`, live keyword test with a sources set.
- [ ] **Step 2:** Run; fail. **Step 3:** Implement. **Step 4:** Unit + changed integration files pass.
- [ ] **Step 5:** Commit `feat(chat): filter answers by document type and tags`.

### Task 5: Frontend data layer and Upload dropdowns

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts` (`getTags()`, `setDocumentTags(id, valueIds)`, `uploadDocument` with `tagIds`, admin calls taking `adminKey` and sending `X-Admin-Key`), `frontend/src/features/library/UploadCard.tsx`, `frontend/src/features/library/validation.ts`
- Create: `frontend/src/features/tags/useTags.ts` (loads `/tags` once; exposes categories, active values per category, a refresh)
- Test: `frontend/src/api/client.test.ts`, `frontend/src/features/library/DocumentsPage.test.tsx` (upload part), `validation.test.ts`

**Interfaces:**
- Produces: `TagCategory`, `TagValue`, `DocumentTag` TS types matching Task 2/3 JSON; `useTags(): { categories: TagCategory[]; loading: boolean; error: string | null; refresh(): void }`.

- [ ] **Step 1:** Tests: upload card shows three selects with only active values (retired hidden), extra categories as single/multi selects, no free-text inputs remain, submit sends codes and tag ids; client sends `X-Admin-Key` only on admin calls.
- [ ] **Step 2:** Run (`npm test`); fail. **Step 3:** Implement (remove the `COMMON_TYPES`/datalist free text). **Step 4:** `npm run typecheck && npm test && npm run build` pass.
- [ ] **Step 5:** Commit `feat(frontend): upload with master-data dropdowns`.

### Task 6: Documents screen tags and Chat filters

**Files:**
- Modify: `frontend/src/features/library/DocumentsPage.tsx`, `DocumentTable.tsx` (Tags column, "retired" / "not in master list" marks, per-category filters, "Edit tags" dialog for extra categories), `frontend/src/features/chat/ChatPage.tsx`, `ChatTurn.tsx` (Product, Version, Document type selects with "Any"; extra categories; scope chips show labels)
- Test: `DocumentsPage.test.tsx`, `ChatPage.test.tsx`

**Interfaces:**
- Consumes: Task 5 `useTags`, client calls; Task 3/4 API shapes.

- [ ] **Step 1:** Tests: documents list shows tags and marks; filtering by a tag narrows the list; edit-tags saves via `setDocumentTags`; chat sends `{product, version, doc_type, tags}` from selects (omitting "Any"); scope chips show labels.
- [ ] **Step 2–4:** fail → implement → typecheck/test/build pass.
- [ ] **Step 5:** Commit `feat(frontend): tags in Documents and Chat`.

### Task 7: Tags admin screen

**Files:**
- Create: `frontend/src/features/tags/TagsPage.tsx`, `TagsPage.test.tsx`
- Modify: `frontend/src/App.tsx` (sidebar entry "Tags")

**Interfaces:**
- Consumes: Task 5 client admin calls and `useTags().refresh`.

- [ ] **Step 1:** Tests: asks for the admin key once and keeps it in `sessionStorage` (never in the URL); lists categories and values with retired shown; add category, add value, rename label, retire/restore, delete unused; a 401 clears the stored key and asks again; a 403 shows "Admin is disabled on this server".
- [ ] **Step 2–4:** fail → implement → typecheck/test/build pass.
- [ ] **Step 5:** Commit `feat(frontend): tags admin screen`.

### Task 8: Docs and full verification

**Files:**
- Modify: `CLAUDE.md` (layout bullet for tags; API endpoints line; Status), `docs/under-the-hood.md`, `docs/langchain-ollama-rag-architecture.md`, `docs/run-locally-windows.md` (ADMIN_KEY), `.env.example` (already from Task 2)

- [ ] **Step 1:** Update the docs concisely, matching their style.
- [ ] **Step 2:** Run the full unit suite, `pytest -m integration` for every integration file this work touched (Docker DB only; skip files that need Ollama and say so), and the frontend checks. Paste the summary lines.
- [ ] **Step 3:** Stop the containers you started.
- [ ] **Step 4:** Commit `docs: master-data tagging`.
- [ ] **Step 5:** Report: commits, test summaries, anything deferred or unsure.
