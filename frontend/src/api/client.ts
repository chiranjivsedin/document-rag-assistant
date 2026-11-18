// Small fetch wrapper for the RAG API. URLs are relative: Vite's dev proxy (vite.config.ts) forwards them to uvicorn.

import type {
  ChatRequest,
  ChatResponse,
  DocumentRecord,
  HealthResponse,
  IngestJob,
  NewTagCategory,
  NewTagValue,
  TagCategory,
  TagValue,
  TagValueChange,
  UploadedDocument,
  UploadFields,
} from "./types";

/** The API answered with an error status; `detail` is its message (or a summary of FastAPI's 422 field errors). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`HTTP ${status}: ${detail}`);
    this.name = "ApiError";
  }
}

/** The API could not be reached at all (server not running, proxy failure, network down). */
export class ApiUnreachableError extends Error {
  constructor(cause: unknown) {
    super("Can't reach the API. Is it running on 127.0.0.1:8000?", { cause });
    this.name = "ApiUnreachableError";
  }
}

async function send(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(path, init);
  } catch (error) {
    if (init?.signal?.aborted) throw error; // the caller cancelled; not a connection problem
    throw new ApiUnreachableError(error);
  }
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined; // e.g. the dev proxy's empty 502 when uvicorn is down
  }
}

/** Turn an error body into one line: FastAPI uses {"detail": "..."}, or a list of field errors for 422. */
export function errorDetail(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item: { loc?: unknown[]; msg?: string }) => {
        const field = item.loc?.filter((part) => part !== "body").join(".");
        return field ? `${field}: ${item.msg}` : (item.msg ?? "invalid input");
      })
      .join("; ");
  }
  return `Request failed with status ${status}`;
}

/** GET /health. A 503 is a normal answer here (it lists the problems), so only other statuses throw. */
export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await send("/health", { signal });
  const body = await readJson(response);
  if ((response.ok || response.status === 503) && isHealthResponse(body)) {
    return body;
  }
  throw failure(response, body);
}

/** POST /chat. Takes 15–60 s on a laptop CPU; abort `signal` to stop waiting (the server still finishes). */
export async function postChat(request: ChatRequest, signal?: AbortSignal): Promise<ChatResponse> {
  const response = await send("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
  const body = await readJson(response);
  if (response.ok) return body as ChatResponse;
  throw failure(response, body);
}

export type IngestStart = { kind: "started"; job: IngestJob } | { kind: "already-running"; jobId: string };

/** POST /documents/ingest. A 409 is not an error here: it names the job that is already running, to follow instead. */
export async function startIngest(signal?: AbortSignal): Promise<IngestStart> {
  const response = await send("/documents/ingest", { method: "POST", signal });
  const body = await readJson(response);
  if (response.ok) return { kind: "started", job: body as IngestJob };
  const runningId = (body as { job_id?: unknown } | undefined)?.job_id;
  if (response.status === 409 && typeof runningId === "string") return { kind: "already-running", jobId: runningId };
  throw failure(response, body);
}

/** GET /documents/ingest/{job_id}. A 404 means the server no longer knows the job (e.g. it was restarted). */
export async function getIngestJob(jobId: string, signal?: AbortSignal): Promise<IngestJob> {
  const response = await send(`/documents/ingest/${encodeURIComponent(jobId)}`, { signal });
  const body = await readJson(response);
  if (response.ok) return body as IngestJob;
  throw failure(response, body);
}

/** GET /documents: every document in the library, most recently added first. */
export async function listDocuments(signal?: AbortSignal): Promise<DocumentRecord[]> {
  const response = await send("/documents", { signal });
  const body = await readJson(response);
  const documents = (body as { documents?: unknown } | undefined)?.documents;
  if (response.ok && Array.isArray(documents)) return documents as DocumentRecord[];
  if (response.ok) throw new ApiError(response.status, "The document list came back in an unexpected form.");
  throw failure(response, body);
}

/**
 * POST /documents (multipart). The server stores the file and starts indexing it when it can; `ingest_job_id` says
 * whether it did. The browser sets the multipart Content-Type (with its boundary) itself.
 */
export async function uploadDocument(file: File, fields: UploadFields, signal?: AbortSignal): Promise<UploadedDocument> {
  const form = new FormData();
  form.append("file", file);
  form.append("product", fields.product);
  form.append("version", fields.version);
  form.append("doc_type", fields.docType);
  for (const id of fields.tagIds ?? []) form.append("tag", String(id));
  const response = await send("/documents", { method: "POST", body: form, signal });
  const body = await readJson(response);
  if (response.ok) return body as UploadedDocument;
  throw failure(response, body);
}

/** DELETE /documents/{id}: 204 when the file, its passages and its record are all gone. */
export async function deleteDocument(id: string, signal?: AbortSignal): Promise<void> {
  const response = await send(`/documents/${encodeURIComponent(id)}`, { method: "DELETE", signal });
  if (response.status === 204) return;
  throw failure(response, await readJson(response));
}

/** PUT /documents/{id}/tags: replace the document's extra tags. Ordinary work: no admin key. */
export async function setDocumentTags(id: string, valueIds: number[], signal?: AbortSignal): Promise<DocumentRecord> {
  const response = await send(`/documents/${encodeURIComponent(id)}/tags`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value_ids: valueIds }),
    signal,
  });
  const body = await readJson(response);
  if (response.ok) return body as DocumentRecord;
  throw failure(response, body);
}

/** GET /tags: every category with all its values (active and retired), built-ins first. Open to everyone. */
export async function getTags(signal?: AbortSignal): Promise<TagCategory[]> {
  const response = await send("/tags", { signal });
  const body = await readJson(response);
  const categories = (body as { categories?: unknown } | undefined)?.categories;
  if (response.ok && Array.isArray(categories)) return categories as TagCategory[];
  if (response.ok) throw new ApiError(response.status, "The tag lists came back in an unexpected form.");
  throw failure(response, body);
}

/**
 * An admin call: JSON body, and the admin key in the X-Admin-Key header (never in the URL, which ends up in logs and
 * the browser history). 401 = missing or wrong key, 403 = admin disabled on the server.
 */
async function adminSend(adminKey: string, path: string, method: string, payload?: unknown): Promise<unknown> {
  const headers: Record<string, string> = { "X-Admin-Key": adminKey };
  if (payload !== undefined) headers["Content-Type"] = "application/json";
  const response = await send(path, {
    method,
    headers,
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  if (response.status === 204) return undefined;
  const body = await readJson(response);
  if (response.ok) return body;
  throw failure(response, body);
}

/** POST /tags/categories (admin): add an extra category. */
export async function addTagCategory(adminKey: string, category: NewTagCategory): Promise<TagCategory> {
  return (await adminSend(adminKey, "/tags/categories", "POST", category)) as TagCategory;
}

/** POST /tags/categories/{key}/values (admin): add an active value. */
export async function addTagValue(adminKey: string, categoryKey: string, value: NewTagValue): Promise<TagValue> {
  const path = `/tags/categories/${encodeURIComponent(categoryKey)}/values`;
  return (await adminSend(adminKey, path, "POST", value)) as TagValue;
}

/** PATCH /tags/values/{id} (admin): rename, retire (active: false) or restore a value. */
export async function updateTagValue(adminKey: string, id: number, change: TagValueChange): Promise<TagValue> {
  return (await adminSend(adminKey, `/tags/values/${id}`, "PATCH", change)) as TagValue;
}

/** DELETE /tags/values/{id} (admin): delete a value nothing uses (409 if it is in use: retire it instead). */
export async function deleteTagValue(adminKey: string, id: number): Promise<void> {
  await adminSend(adminKey, `/tags/values/${id}`, "DELETE");
}

/** Where the original file is served: shown in the browser, or saved with `download`. A plain link, no fetch. */
export function documentFileUrl(id: string, download = false): string {
  return `/documents/${encodeURIComponent(id)}/file${download ? "?download=true" : ""}`;
}

function failure(response: Response, body: unknown): Error {
  if (response.status >= 500 && body === undefined) {
    return new ApiUnreachableError(`proxy returned ${response.status} without a body`);
  }
  return new ApiError(response.status, errorDetail(body, response.status));
}

function isHealthResponse(body: unknown): body is HealthResponse {
  const value = body as HealthResponse | undefined;
  return (value?.status === "ok" || value?.status === "unavailable") && Array.isArray(value.problems);
}
