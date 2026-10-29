import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { IngestJob } from "../../api/types";
import { IngestPage } from "./IngestPage";

const JOB_ID = "3f9c0a1b2c3d4e5f60718293a4b5c6d7";
const STARTED = "2026-10-29T10:15:00.000Z";

function job(overrides: Partial<IngestJob> = {}): IngestJob {
  return {
    job_id: JOB_ID,
    status: "running",
    started_at: STARTED,
    finished_at: null,
    report: null,
    error: null,
    ...overrides,
  };
}

const SUCCEEDED = job({
  status: "succeeded",
  finished_at: "2026-10-29T10:15:10.700Z",
  report: {
    added: ["documentum/16.2/release-notes/notes.pdf"],
    updated: ["documentum/16.2/deprecated-features/list.md"],
    unchanged: ["acme-dms/1.0/release-notes/release-notes.md", "acme-dms/2.0/release-notes/release-notes.md"],
    removed: ["acme-dms/0.9/old.md"],
    no_text: ["documentum/16.2/release-notes/scan.pdf"],
    chunks_stored: 42,
  },
});

type Reply = () => Promise<Response>;
const json = (body: unknown, status = 200): Reply => async () => Response.json(body, { status });

/** fetch stand-in: `start` answers POST /documents/ingest; `polls` answer each GET in turn, then stay pending. */
function api(start: Reply, ...polls: Reply[]) {
  const calls: string[] = [];
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${url}`);
    if (init?.method === "POST") return start();
    const next = polls.shift();
    return next ? next() : new Promise<Response>(() => {}); // no more scripted answers: the job looks stuck
  });
  vi.stubGlobal("fetch", fetch);
  return { calls };
}

async function clickIndex() {
  await userEvent.click(screen.getByRole("button", { name: "Index documents" }));
}

function indexButton() {
  return screen.getByRole("button", { name: "Index documents" }) as HTMLButtonElement;
}

afterEach(() => {
  vi.useRealTimers();
});

describe("IngestPage: a job that runs to the end", () => {
  it("starts a job, polls it, and shows the report grouped like ingest.py", async () => {
    const { calls } = api(json(job(), 202), json(job()), json(SUCCEEDED));
    render(<IngestPage pollIntervalMs={10} />);
    await clickIndex();

    const result = await screen.findByText(/Indexing finished in 10.7 s/);
    expect(result.parentElement!.textContent).toContain(
      "1 added, 1 updated, 2 unchanged, 1 removed, 1 with no text · 42 chunks stored.",
    );
    expect(screen.getByRole("heading", { name: "Added (1)" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Updated (1)" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Removed (1)" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "No text found (skipped) (1)" })).toBeTruthy();
    expect(screen.getByText("documentum/16.2/release-notes/notes.pdf")).toBeTruthy();
    expect(screen.getByText("2 unchanged files")).toBeTruthy(); // collapsed by default
    expect(calls).toEqual([
      "POST /documents/ingest",
      `GET /documents/ingest/${JOB_ID}`,
      `GET /documents/ingest/${JOB_ID}`,
    ]);
    expect(indexButton().disabled).toBe(false);
  });

  it("says so when nothing changed", async () => {
    const upToDate = job({
      status: "succeeded",
      finished_at: "2026-10-29T10:15:01.000Z",
      report: { added: [], updated: [], unchanged: ["a.md"], removed: [], no_text: [], chunks_stored: 0 },
    });
    api(json(job(), 202), json(upToDate));
    render(<IngestPage pollIntervalMs={10} />);
    await clickIndex();
    expect(await screen.findByText("Everything was already up to date.")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /Added/ })).toBeNull();
    expect(screen.getByText("1 unchanged file")).toBeTruthy();
  });

  it("shows progress while the job runs and blocks a second start", async () => {
    api(json(job(), 202)); // the job never finishes
    render(<IngestPage pollIntervalMs={10} />);
    await clickIndex();
    const status = await screen.findByText("Indexing…");
    const panel = status.closest("[role=status]")!;
    expect(panel.textContent).toMatch(/\d+ s/); // elapsed since started_at
    expect(panel.textContent).toContain("Job 3f9c0a1b · status checked every 0.01 s.");
    expect(indexButton().disabled).toBe(true);
  });

  it("shows 'Starting…' until the API accepts the job", async () => {
    api(() => new Promise<Response>(() => {}));
    render(<IngestPage />);
    await clickIndex();
    expect(screen.getByRole("status").textContent).toContain("Starting…");
    expect(indexButton().disabled).toBe(true);
  });
});

describe("IngestPage: failures", () => {
  it("shows a failed job's reason and allows another run", async () => {
    const failed = job({
      status: "failed",
      finished_at: "2026-10-29T10:15:03.000Z",
      error: "The model service failed: model nomic-embed-text not found",
    });
    api(json(job(), 202), json(failed));
    render(<IngestPage pollIntervalMs={10} />);
    await clickIndex();
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Indexing failed. The model service failed: model nomic-embed-text not found",
    );
    expect(screen.getByText(/Files indexed before the failure stay indexed/)).toBeTruthy();
    expect(indexButton().disabled).toBe(false);
  });

  it("explains a job that disappeared (404 after a server restart) and stops polling", async () => {
    const lost = json({ detail: "Unknown ingestion job. Job history is kept in memory and is lost when the server restarts." }, 404);
    const { calls } = api(json(job(), 202), json(job()), lost);
    render(<IngestPage pollIntervalMs={10} />);
    await clickIndex();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("The server no longer knows this job");
    expect(alert.textContent).toContain("job history is kept only in the server's memory");
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(calls).toHaveLength(3); // POST + 2 GETs, none after the 404
    expect(indexButton().disabled).toBe(false);
  });

  it.each([
    [
      503,
      { detail: "Can't reach Ollama at http://127.0.0.1:11434. Is it running? Start Ollama, or check OLLAMA_BASE_URL." },
      "A required service is unavailable. Can't reach Ollama at http://127.0.0.1:11434. Is it running? Start Ollama, or check OLLAMA_BASE_URL.",
    ],
    [
      500,
      { detail: "The documents folder (DOCUMENTS_DIR) doesn't exist on the server." },
      "Something went wrong on the server. The documents folder (DOCUMENTS_DIR) doesn't exist on the server.",
    ],
  ])("shows why the job could not start (HTTP %i)", async (status, body, message) => {
    const { calls } = api(json(body, status));
    render(<IngestPage pollIntervalMs={10} />);
    await clickIndex();
    expect((await screen.findByRole("alert")).textContent).toBe(message);
    expect(calls).toEqual(["POST /documents/ingest"]);
    expect(indexButton().disabled).toBe(false);
  });

  it("says the API is unreachable when the start request fails", async () => {
    api(async () => Promise.reject(new TypeError("Failed to fetch")));
    render(<IngestPage pollIntervalMs={10} />);
    await clickIndex();
    expect((await screen.findByRole("alert")).textContent).toContain("Can't reach the API");
  });

  it("keeps polling through a temporary loss of contact", async () => {
    const blip: Reply = async () => Promise.reject(new TypeError("Failed to fetch"));
    api(json(job(), 202), blip, json(SUCCEEDED));
    render(<IngestPage pollIntervalMs={30} />);
    await clickIndex();
    expect((await screen.findByText(/Couldn't check the job status/)).textContent).toContain("Still trying");
    expect(await screen.findByText(/Indexing finished/)).toBeTruthy();
    expect(screen.queryByText(/Couldn't check the job status/)).toBeNull();
  });
});

describe("IngestPage: following a job started elsewhere (409)", () => {
  it("follows the running job the API names, checking its status straight away", async () => {
    const otherId = "aaaabbbbccccddddeeeeffff00001111";
    const conflict = json({ detail: "An ingestion job is already running.", job_id: otherId }, 409);
    const { calls } = api(conflict, json(job({ job_id: otherId })), json({ ...SUCCEEDED, job_id: otherId }));
    render(<IngestPage pollIntervalMs={10} />);
    await clickIndex();
    expect(await screen.findByText(/An indexing job was already running/)).toBeTruthy();
    expect(await screen.findByText(/Indexing finished/)).toBeTruthy();
    expect(calls.slice(0, 2)).toEqual(["POST /documents/ingest", `GET /documents/ingest/${otherId}`]);
  });
});

describe("IngestPage: polling interval", () => {
  it("checks the job every 2 seconds by default", async () => {
    vi.useFakeTimers();
    const { calls } = api(json(job(), 202), json(job()), json(job()), json(job()));
    render(<IngestPage />);
    const polls = () => calls.filter((call) => call.startsWith("GET")).length;

    fireEvent.click(screen.getByRole("button", { name: "Index documents" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0); // the POST answers
    });
    expect(polls()).toBe(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1999);
    });
    expect(polls()).toBe(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(polls()).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(polls()).toBe(2);
  });
});
