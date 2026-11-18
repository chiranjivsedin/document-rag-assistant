// Response bodies of the HTTP API, mirroring src/rag/api/schemas.py. Keep the two in step.

export interface HealthResponse {
  status: "ok" | "unavailable";
  llm_model: string;
  embedding_model: string;
  problems: string[]; // one plain-English line per problem; empty when status is "ok"
}

/** What one ingestion run did; each list holds paths relative to the documents folder. */
export interface IngestReport {
  added: string[];
  updated: string[];
  unchanged: string[];
  removed: string[];
  no_text: string[]; // files with no extractable text (e.g. scanned PDFs), skipped
  chunks_stored: number;
}

export interface IngestJob {
  job_id: string;
  status: "running" | "succeeded" | "failed";
  started_at: string; // ISO 8601, UTC
  finished_at: string | null;
  report: IngestReport | null; // set when status is "succeeded"
  error: string | null; // set when status is "failed"
}

/** Passages per question when the user doesn't choose: the server's default (DEFAULT_K in retrieval/search.py). */
export const DEFAULT_TOP_K = 3;

export interface ChatRequest {
  question: string; // 1–2000 characters after trimming
  product?: string; // optional filters (codes from the master lists); leave out for "no filter"
  version?: string;
  doc_type?: string;
  tags?: Record<string, string[]>; // extra category key -> codes: every category must match, any of its codes
  k?: number; // passages to retrieve, 1–20 (server default DEFAULT_TOP_K)
}

export interface ChatSource {
  number: number; // matches the [n] citations in the answer
  source: string | null;
  product: string | null;
  version: string | null;
  doc_type: string | null;
  page: number | null;
  distance: number; // cosine distance: 0 = same meaning, larger = less related
  excerpt: string; // the whole passage the model was given
}

export interface ChatResponse {
  answer: string;
  no_answer: boolean; // true when the documents don't contain the answer; sources is then empty
  sources: ChatSource[];
  model: string;
  elapsed_ms: number;
}

// The document library (src/rag/api/documents.py, src/rag/dms/).

export type DocumentStatus = "pending" | "processing" | "indexed" | "failed";

export interface DocumentRecord {
  id: string;
  source: string; // path in the documents folder, as in a chat source
  filename: string; // the name to show: the uploaded file's own name
  product: string | null; // null only for files found higher up the folder tree
  version: string | null;
  doc_type: string | null;
  content_type: string;
  size_bytes: number;
  file_hash: string;
  origin: "upload" | "folder"; // folder: found in the documents folder, not uploaded here
  status: DocumentStatus;
  error: string | null; // set when status is "failed"
  chunk_count: number | null; // passages in the index, once indexed
  added_at: string; // ISO 8601, UTC
  indexed_at: string | null;
  tags: DocumentTag[]; // its extra tags (product, version and doc_type are the fields above)
  unlisted: string[]; // "product", "version" or "doc_type" when its value isn't in the master list
}

export interface UploadedDocument extends DocumentRecord {
  ingest_job_id: string | null; // the indexing job this upload started; null when it couldn't start one
}

export interface UploadFields {
  product: string; // codes from the master lists
  version: string;
  docType: string;
  tagIds?: number[]; // extra tags: tag value ids
}

// Master data for tags (src/rag/api/tags.py, src/rag/dms/tags.py).

/** The keys of the three built-in categories: the document's folders and fields. */
export const BUILTIN_KEYS = ["product", "version", "doc_type"] as const;
export type BuiltinKey = (typeof BUILTIN_KEYS)[number];

export interface TagValue {
  id: number;
  code: string; // stable: what documents store (for a built-in category, the folder name)
  label: string; // the name to show
  active: boolean; // false = retired: no new use, still shown and still filtering
}

export interface TagCategory {
  key: string; // product, version, doc_type, or an extra category's key
  label: string;
  builtin: boolean;
  multiple: boolean; // whether a document may have several values
  values: TagValue[]; // active and retired, in sort order
}

/** One of a document's extra tags. */
export interface DocumentTag {
  id: number; // the tag value's id
  category: string; // the extra category's key
  code: string;
  label: string;
  active: boolean;
}

export interface NewTagCategory {
  key: string;
  label: string;
  multiple: boolean;
}

export interface NewTagValue {
  code: string;
  label: string;
}

/** Only the fields given change; a code never changes. */
export interface TagValueChange {
  label?: string;
  active?: boolean;
  sort_order?: number;
}
