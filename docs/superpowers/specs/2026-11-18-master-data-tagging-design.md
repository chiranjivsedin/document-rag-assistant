# Master-data tagging: design

Date: 2026-11-18 · Branch: `feature/master-data-tagging` (from `integration` at `612eabf`) · Approach chosen: **A**

## Intent

The project lead asked: *"include metadata tagging feature for the documents; these tags should have master data, not ad-hoc."* Every document in the library is described by tags picked from centrally managed lists (master data). Nobody types a tag value freely, so the same thing is always spelled the same way and filtering by tag is reliable.

The lead didn't say what the tags are for. Our interpretation (to be shown to the lead), as a RAG app:

1. **Documents screen:** each document shows its tags; the list can be filtered by them.
2. **Chat:** a question can be narrowed to documents with chosen tags (e.g. Product = Content Management, Type = Release notes). The planned version-comparison feature will build on this.

The source documents now are 43 OpenText Content Suite / Content Management PDFs: release notes for 30 versions 16.2–26.4, and core guides (Installation, Upgrade, System Administration) for 16.2, 20.4, 22.4, 24.4 and 26.4.

## Goals

- **Master lists:** tag *categories*, each with a list of allowed *values*, stored in the database and managed in the app.
- **Three built-in categories:** Product, Version, Document type. They are today's `product` / `version` / `doc_type` fields, which become pick-from-list instead of free text.
- **Extra categories:** e.g. Module or Audience, added in the app later without code changes (the lead's categories aren't known yet).
- **Upload:** dropdowns from the master lists only, no free text.
- **Documents screen:** tags shown; filter by any category; edit a document's extra-category tags.
- **Chat:** filter by built-in and extra tags (dropdowns, replacing today's free-text Product and Version fields).
- **Admin screen:** add categories and values, rename a value's label, retire or restore a value. Protected by an admin key.
- **Nothing existing breaks:** documents already in the library and the index keep working, untagged or not; **no re-indexing**.

## Non-goals

- User accounts, logins or roles (the admin key is the only protection).
- Automatic tagging (e.g. Claude suggesting tags): later, after the Claude API switch.
- Version *ranges* in filters ("22.4 to 24.4") and the version-comparison feature itself: separate work.
- Changing a document's Product, Version or Document type after upload (it would mean moving the file on disk); re-upload instead.
- Hierarchical tags (value trees), tag synonyms, translations.

## Anti-goals (a failure even if it technically works)

- Any place where a user can still type a tag value freely.
- Existing documents disappearing from lists or answers, or needing re-indexing.
- Deleting a value that documents use (silent data loss). Values are retired, never deleted while in use.
- The admin key ending up in the repository, logs, the browser's URL or the question log.

## Constraints (from the project, see CLAUDE.md)

- Files stay at `DOCUMENTS_DIR/<product>/<version>/<doc_type>/<file>`; ingestion takes product/version/doc_type from those folders; they are real columns on every chunk (`METADATA_COLUMNS`) and search filters them in SQL. This doesn't change.
- Built-in values must be valid folder names (`storage.check_folder_name()`: letters, digits, dots, hyphens, underscores; the frontend repeats this in `validation.ts`).
- One uvicorn worker; endpoints are plain `def`; services built on first use; LangChain only behind our own functions; shared glue in `service.py`.
- The library reaches the `chunks` table only through `IndexAccess`.
- The repository is public: no client data or secrets in code, seeds or tests.

## Approaches considered

| | Approach | Trade-off | Decision |
|---|---|---|---|
| **A** | Master lists in the database, on top of today's folders: built-in categories validate the existing three fields; extra categories live only in tag tables; chat filters extra tags by joining documents to their tags | Two kinds of tag under the hood (built-in vs extra); one experience for users. No re-index, nothing to migrate | **Chosen**: delivers the ask on what already works |
| B | Everything becomes a tag; files stored by ID, detached from folders | Cleanest model, but rewrites storage, ingestion, chunk metadata and search, needs a full re-index and a VM migration | Rejected: cost and risk far beyond the ask |
| C | Master lists in a config file in the repository, no admin screen | Simplest, no auth question; but lists can't be managed in the app | Rejected: misses "managed master data" |

## Design (decided)

### Data model

- **`tag_categories`**: `id`, `key` (stable, e.g. `product`, `version`, `doc_type`, `module`), `label` (shown), `builtin` (true for the three), `multiple` (whether a document may have several values; false for built-ins), `sort_order`, `created_at`.
- **`tag_values`**: `id`, `category_id`, `code` (stable, what is stored on documents; for built-ins it is the folder name, e.g. `content-management`, `24.4`, `release-notes`), `label` (shown, e.g. "Content Management", "24.4", "Release notes"), `active` (false = retired), `sort_order` (versions sort numerically, not alphabetically), `created_at`. Unique per category: `code`, and `label` among active values.
- **`document_tags`**: `document_id` → `documents.id`, `value_id` → `tag_values.id`; only for **extra** categories (built-ins stay in the `documents` row's product/version/doc_type columns).
- **Codes are never renamed** (they are folder names and stored references). Renaming changes the label only.

### Seed and existing data

- On first use, the tables are created and seeded **if empty**:
  - Product: `content-management` "Content Management".
  - Version: exactly these 30 (labels = codes), sorted numerically: 16.2, 16.4, 16.6, 16.7, 20.2, 20.3, 20.4, 21.1, 21.2, 21.3, 21.4, 22.1, 22.2, 22.3, 22.4, 23.1, 23.2, 23.3, 23.4, 24.1, 24.2, 24.3, 24.4, 25.1, 25.2, 25.3, 25.4, 26.1, 26.2, 26.4. (No 26.3: OpenText published none for this product.)
  - Document type: `release-notes` "Release notes", `installation-guide` "Installation guide", `upgrade-guide` "Upgrade guide", `admin-guide` "Administration guide".
- **Existing documents are only test samples** (decided 2026-11-18): their values (e.g. `acme-dms`, `AcmeDocs`, `documentum`) are **not imported** into the lists. They keep working and are shown "not in master list" until they are removed.
- Documents whose built-in value isn't in the list (e.g. files copied into the folder by hand) still show and answer; the Documents screen marks the value "not in master list".

### Which documents, under which folders (decided 2026-11-18)

- **One technology:** the lead chose OpenText Content Suite. The library will hold only the Content Suite / Content Management documents; the **Documentum 23.4 files and the test files (`acme-dms`, `AcmeDocs`, `demo-product`) are removed** when the new documents are loaded.
- **Folder names = seed codes**, so dropdowns match the files exactly: `documents/content-management/<version>/<doc_type>/<file>.pdf`, with doc_type `release-notes`, `installation-guide`, `upgrade-guide` or `admin-guide`.
- **The 43 PDFs** are outside the repository (client-licensed material, never committed), in the deployer's `Downloads\opentext-content-server\<version>\`: one release-notes PDF per version, plus `Content Server <version> - Installation|Upgrade|System Administration Guide.pdf` for 16.2, 20.4, 22.4, 24.4, 26.4 (System Administration only for 22.4, 24.4, 26.4).
- **Loading is a separate step after this build** (ideally after the Claude API switch, for indexing speed): copy the files into the folders above and let ingestion adopt them (existing behaviour), then delete the Documentum and test documents through the library and delete their now-unused values. The upload card is for one-off documents. This work only needs to make that path possible (adopted files get their built-in values from their folders; values not in the list are shown "not in master list").
- **The VM runbook** (still planned around Documentum: A11, 6.9, check 16) is updated when this is deployed, not in this work.

### Rules

- Upload accepts only **active** values of the built-in categories, and extra tags only from active values of extra categories (single-value categories: at most one value).
- Retiring a value hides it from dropdowns and blocks new use; documents keeping it still show it (marked "retired") and still filter by it.
- A value can be deleted only if nothing uses it (no document, no chunk folder); otherwise only retired.

### API (all under the existing API; JSON errors as today)

- `GET /tags`: categories with their values (active and retired, each flagged), in sort order. Open to everyone (needed for dropdowns).
- Admin, each requiring header **`X-Admin-Key`** equal to setting **`ADMIN_KEY`** (constant-time comparison; if `ADMIN_KEY` is empty the admin endpoints answer 403 "Admin is disabled"; a wrong or missing key gets 401):
  - `POST /tags/categories` (key, label, multiple)
  - `POST /tags/categories/{key}/values` (code, label; built-in codes must pass `check_folder_name`)
  - `PATCH /tags/values/{id}` (label, active, sort_order)
  - `DELETE /tags/values/{id}` (409 if in use)
- `POST /documents` (upload): product, version, doc_type must be active codes (422 otherwise); optional extra tags.
- `PUT /documents/{id}/tags`: replace a document's **extra** tags. **Not admin-protected** (tagging a document is ordinary work; only the lists are protected).
- `GET /documents`: each document returns its extra tags; optional filters by any category.
- `POST /chat`: optional `tags` (category key → list of codes) in addition to `product`/`version`; built-ins filter as today; extra tags restrict passages to documents having the chosen values (all chosen categories must match; within a category, any of the chosen values).
- The question log records the tags used.

### Frontend

- **Upload card:** three dropdowns (Product, Version, Document type) from active values, plus a control per extra category (single select or multi select). No free-text inputs.
- **Documents screen:** a Tags column (built-ins and extras, retired / not-in-list marked), filters per category, and an "Edit tags" action for extra tags.
- **Chat:** Product and Version become dropdowns (with "Any"), plus Document type and extra categories; a small "Tags" area so the screen doesn't get crowded.
- **Tags admin screen** (new sidebar entry): lists per category; add category, add value, rename label, retire / restore, delete unused. Asks for the admin key once; keeps it in the browser's session storage for that tab only; never in the URL.
- `validation.ts` mirrors the server's code rules.

### Settings

- `ADMIN_KEY`: empty by default (admin disabled). On the VM, a long random value kept outside the repository, like the database password.

## Left to the builder

- Table and module names, SQL details, and how the extra-tag filter reaches both the vector search and the keyword search (e.g. a list of allowed `source` values passed as an `IN` filter), as long as it stays SQL with parameters and needs no re-index.
- Exact screen layout and component structure, within the existing app shell and styles.
- How the seed and the import of existing values run (on first use, idempotent).
- Test structure: unit tests with fakes as today, integration tests against the real database for the new tables and filters, frontend tests for the new controls.
- Docs: CLAUDE.md, `.env.example`, `docs/under-the-hood.md`, the architecture doc, the run guide.

## Calls made without the lead (to confirm with them later)

- Tags are used for both the Documents screen and chat filtering.
- Starting categories are the three built-ins; the lead's own categories are added through the admin screen.
- An admin key protects the lists; tagging an individual document needs no key.
- Bulk loading by folder copy and adoption rather than 43 uploads; the runbook is updated at deployment, not now.
- Existing sample documents are not worth migrating: their values are not imported, and they are removed when the Content Suite documents are loaded.
