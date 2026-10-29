import { describe, expect, it, vi } from "vitest";

import { ApiError, ApiUnreachableError, errorDetail, getHealth, getIngestJob, postChat, startIngest } from "./client";

const OK = { status: "ok", llm_model: "gemma3:4b", embedding_model: "nomic-embed-text", problems: [] };
const DOWN = {
  status: "unavailable",
  llm_model: "gemma3:4b",
  embedding_model: "nomic-embed-text",
  problems: ["Can't reach Ollama at http://127.0.0.1:11434. Is it running? Start Ollama, or check OLLAMA_BASE_URL."],
};

function respond(status: number, body?: unknown) {
  const fetch = vi.fn(async () =>
    body === undefined ? new Response(null, { status }) : Response.json(body, { status }),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("getHealth", () => {
  it("returns the body on 200 and calls the relative /health URL", async () => {
    const fetch = respond(200, OK);
    expect(await getHealth()).toEqual(OK);
    expect(fetch).toHaveBeenCalledWith("/health", expect.anything());
  });

  it("treats 503 as an answer listing the problems, not as an error", async () => {
    respond(503, DOWN);
    expect(await getHealth()).toEqual(DOWN);
  });

  it("throws ApiError with the server's detail for other statuses", async () => {
    respond(404, { detail: "Not Found" });
    const error = await getHealth().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, detail: "Not Found" });
  });

  it("reports the API as unreachable when fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(getHealth()).rejects.toBeInstanceOf(ApiUnreachableError);
  });

  it("reports the API as unreachable when the dev proxy answers 502 with no body (uvicorn not running)", async () => {
    respond(502); // what Vite's proxy actually returns on ECONNREFUSED
    await expect(getHealth()).rejects.toBeInstanceOf(ApiUnreachableError);
  });

  it("keeps a real 502 from the API (it has a JSON detail) as an ApiError", async () => {
    respond(502, { detail: "The model service failed: out of memory" });
    await expect(getHealth()).rejects.toMatchObject({ status: 502, detail: "The model service failed: out of memory" });
  });
});

describe("postChat", () => {
  const ANSWER = { answer: "A [1].", no_answer: false, sources: [], model: "gemma3:4b", elapsed_ms: 1200 };

  it("POSTs the request as JSON to /chat and returns the answer", async () => {
    const fetch = respond(200, ANSWER);
    expect(await postChat({ question: "Q?", version: "16.2", k: 3 })).toEqual(ANSWER);
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/chat");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    expect(JSON.parse(init.body as string)).toEqual({ question: "Q?", version: "16.2", k: 3 });
  });

  it("throws ApiError with the detail for error statuses", async () => {
    respond(503, { detail: "Ollama or the database stopped responding. Check GET /health." });
    await expect(postChat({ question: "Q?" })).rejects.toMatchObject({
      status: 503,
      detail: "Ollama or the database stopped responding. Check GET /health.",
    });
  });

  it("passes a cancellation through as an abort, not as 'unreachable'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new DOMException("Aborted", "AbortError"))),
    );
    const controller = new AbortController();
    controller.abort();
    const error = await postChat({ question: "Q?" }, controller.signal).catch((e: unknown) => e);
    expect(error).not.toBeInstanceOf(ApiUnreachableError);
    expect((error as DOMException).name).toBe("AbortError");
  });
});

describe("ingestion", () => {
  const JOB = {
    job_id: "abc",
    status: "running",
    started_at: "2026-10-29T10:15:00Z",
    finished_at: null,
    report: null,
    error: null,
  };

  it("startIngest POSTs with no body and returns the new job on 202", async () => {
    const fetch = respond(202, JOB);
    expect(await startIngest()).toEqual({ kind: "started", job: JOB });
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/documents/ingest");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });

  it("startIngest turns a 409 into the running job to follow", async () => {
    respond(409, { detail: "An ingestion job is already running.", job_id: "running-job" });
    expect(await startIngest()).toEqual({ kind: "already-running", jobId: "running-job" });
  });

  it("startIngest throws for a 409 without a job id, and for other errors", async () => {
    respond(409, { detail: "Conflict" });
    await expect(startIngest()).rejects.toMatchObject({ status: 409, detail: "Conflict" });
    respond(503, { detail: "Can't connect to the database." });
    await expect(startIngest()).rejects.toMatchObject({ status: 503 });
  });

  it("getIngestJob reads the job and escapes the id in the URL", async () => {
    const fetch = respond(200, JOB);
    expect(await getIngestJob("a/b")).toEqual(JOB);
    expect((fetch.mock.calls[0] as unknown as [string])[0]).toBe("/documents/ingest/a%2Fb");
  });

  it("getIngestJob throws ApiError 404 for an unknown job", async () => {
    respond(404, { detail: "Unknown ingestion job." });
    const error = await getIngestJob("gone").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404 });
  });
});

describe("errorDetail", () => {
  it("passes a plain detail through", () => {
    expect(errorDetail({ detail: "Internal server error." }, 500)).toBe("Internal server error.");
  });

  it("summarises FastAPI's 422 field errors", () => {
    const body = {
      detail: [
        { type: "less_than_equal", loc: ["body", "k"], msg: "Input should be less than or equal to 20" },
        { type: "missing", loc: ["body", "question"], msg: "Field required" },
      ],
    };
    expect(errorDetail(body, 422)).toBe("k: Input should be less than or equal to 20; question: Field required");
  });

  it("falls back to the status when there is no detail", () => {
    expect(errorDetail(undefined, 502)).toBe("Request failed with status 502");
  });
});
