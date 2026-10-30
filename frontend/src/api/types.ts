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

export interface ChatRequest {
  question: string; // 1–2000 characters after trimming
  product?: string; // optional filters; leave out for "no filter"
  version?: string;
  k?: number; // passages to retrieve, 1–20 (server default 5)
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
