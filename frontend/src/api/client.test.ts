import { describe, expect, it, vi } from "vitest";

import { ApiError, ApiUnreachableError, errorDetail, getHealth } from "./client";

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
