// Small fetch wrapper for the RAG API. URLs are relative: Vite's dev proxy (vite.config.ts) forwards them to uvicorn.

import type { ChatRequest, ChatResponse, HealthResponse, IngestJob } from "./types";

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
