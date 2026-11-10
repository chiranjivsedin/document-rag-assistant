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
  product?: string; // optional filters; leave out for "no filter"
  version?: string;
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
}

export interface UploadedDocument extends DocumentRecord {
  ingest_job_id: string | null; // the indexing job this upload started; null when it couldn't start one
}

export interface UploadFields {
  product: string;
  version: string;
  docType: string;
}
