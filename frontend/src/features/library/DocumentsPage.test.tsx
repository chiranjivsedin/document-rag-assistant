import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DocumentRecord, UploadedDocument } from "../../api/types";
import { DocumentsPage } from "./DocumentsPage";

function doc(overrides: Partial<DocumentRecord>): DocumentRecord {
  return {
    id: "id-indexed",
    source: "documentum/16.2/release-notes/notes.pdf",
    filename: "Release Notes.pdf",
    product: "documentum",
    version: "16.2",
    doc_type: "release-notes",
    content_type: "application/pdf",
    size_bytes: 2_458,
    file_hash: "a".repeat(64),
    origin: "upload",
    status: "indexed",
    error: null,
    chunk_count: 3,
    added_at: "2026-11-02T09:00:00Z",
    indexed_at: "2026-11-02T09:01:00Z",
    ...overrides,
  };
}

const INDEXED = doc({});
const PENDING = doc({ id: "id-pending", source: "acme/1.0/notes/waiting.md", filename: "waiting.md", product: "acme",
  version: "1.0", doc_type: "notes", status: "pending", chunk_count: null, indexed_at: null });
const FAILED = doc({ id: "id-failed", source: "acme/1.0/notes/scan.pdf", filename: "scan.pdf", product: "acme", version: "1.0",
  doc_type: "notes", status: "failed", chunk_count: null, indexed_at: null,
  error: "No text could be extracted (for example a scanned PDF without a text layer)." });
const FOLDER = doc({ id: "id-folder", source: "acme/readme.txt", filename: "readme.txt", product: "acme", version: null,
  doc_type: null, origin: "folder", content_type: "text/plain" });

type Handler = (init: RequestInit | undefined, call: number) => Response | Promise<Response>;

/**
 * fetch stand-in routed by "METHOD path". A list of handlers answers successive calls in turn (the last one repeats).
 * Unrouted calls fail the test.
 */
function server(routes: Record<string, Handler | Handler[]>) {
  const calls: Array<{ key: string; init?: RequestInit }> = [];
  const counts: Record<string, number> = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      calls.push({ key, init });
      const route = routes[key];
      if (!route) throw new Error(`unexpected request: ${key}`);
      const call = (counts[key] = (counts[key] ?? 0) + 1);
      const handlers = Array.isArray(route) ? route : [route];
      return handlers[Math.min(call, handlers.length) - 1](init, call);
    }),
  );
  return { calls, count: (key: string) => calls.filter((c) => c.key === key).length };
}

const list = (...documents: DocumentRecord[]): Handler => () => Response.json({ documents });
const status = (code: number, body?: unknown): Handler => () =>
  body === undefined ? new Response(null, { status: code }) : Response.json(body, { status: code });

function rows() {
  return screen.queryAllByRole("row").slice(1); // skip the header row
}

function rowFor(name: string) {
  const row = rows().find((r) => within(r).queryByText(name));
  if (!row) throw new Error(`no row for ${name}`);
  return row;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listing", () => {
  it("shows each document with its metadata, status and links", async () => {
    server({ "GET /documents": list(INDEXED, PENDING, FAILED, FOLDER) });
    render(<DocumentsPage active />);

    await screen.findByText("Release Notes.pdf");
    expect(rows()).toHaveLength(4);

    const indexed = rowFor("Release Notes.pdf");
    expect(within(indexed).getByText("documentum/16.2/release-notes/notes.pdf")).toBeTruthy();
    expect(within(indexed).getByText("Indexed").textContent).toBe("Indexed · 3 passages");
    expect(within(indexed).getByText("2.4 KB")).toBeTruthy();
    expect(within(indexed).getByRole("link", { name: "Open Release Notes.pdf" }).getAttribute("href")).toBe(
      "/documents/id-indexed/file",
    );
    expect(within(indexed).getByRole("link", { name: "Open Release Notes.pdf" }).getAttribute("target")).toBe("_blank");
    expect(within(indexed).getByRole("link", { name: "Download Release Notes.pdf" }).getAttribute("href")).toBe(
      "/documents/id-indexed/file?download=true",
    );

    expect(within(rowFor("waiting.md")).getByText("Pending")).toBeTruthy();
    expect(within(rowFor("scan.pdf")).getByText(/No text could be extracted/)).toBeTruthy();
    const folder = rowFor("readme.txt");
    expect(within(folder).getByText(/found in the documents folder/)).toBeTruthy();
    expect(within(rowFor("Release Notes.pdf")).queryByText(/found in the documents folder/)).toBeNull(); // uploaded
    expect(within(folder).getByTitle("Product").textContent).toBe("acme"); // no version or type: only its product shows
    expect(within(folder).queryByTitle("Version")).toBeNull();

    const summary = screen.getByLabelText("Library summary");
    expect(summary.textContent).toBe("Documents4Indexed2Waiting1Failed1");
  });

  it("doesn't load until the tab is opened, then loads each time it is opened", async () => {
    const api = server({ "GET /documents": list(INDEXED) });
    const { rerender } = render(<DocumentsPage active={false} />);
    expect(api.count("GET /documents")).toBe(0);

    rerender(<DocumentsPage active />);
    await screen.findByText("Release Notes.pdf");
    rerender(<DocumentsPage active={false} />);
    rerender(<DocumentsPage active />);
    await waitFor(() => expect(api.count("GET /documents")).toBe(2));
  });

  it("explains how to add documents when there are none", async () => {
    server({ "GET /documents": list() });
    render(<DocumentsPage active />);
    expect(await screen.findByText(/No documents yet/)).toBeTruthy();
  });

  it("reports a list that can't load, and Refresh tries again", async () => {
    server({ "GET /documents": [status(503, { detail: "Ollama or the database stopped responding. Check GET /health." }), list(INDEXED)] });
    render(<DocumentsPage active />);

    expect((await screen.findByRole("alert")).textContent).toContain("A required service is unavailable");
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("Release Notes.pdf")).toBeTruthy();
  });

  it("keeps the last list when a refresh fails", async () => {
    server({ "GET /documents": [list(INDEXED), () => Promise.reject(new TypeError("Failed to fetch"))] });
    render(<DocumentsPage active />);
    await screen.findByText("Release Notes.pdf");

    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText(/Couldn't refresh the list/)).toBeTruthy();
    expect(screen.getByText("Release Notes.pdf")).toBeTruthy();
  });
});

describe("search and filter", () => {
  it("filters by text and by status, and says when nothing matches", async () => {
    server({ "GET /documents": list(INDEXED, PENDING, FAILED, FOLDER) });
    render(<DocumentsPage active />);
    await screen.findByText("Release Notes.pdf");

    const search = screen.getByRole("searchbox", { name: "Search documents" });
    await userEvent.type(search, "acme/1.0");
    expect(rows().map((r) => within(r).getByRole("strong").textContent)).toEqual(["waiting.md", "scan.pdf"]);
    expect(screen.getByText(/Showing 2 of 4 documents/)).toBeTruthy();

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Filter by status" }), "failed");
    expect(rows().map((r) => within(r).getByRole("strong").textContent)).toEqual(["scan.pdf"]);

    await userEvent.clear(search);
    await userEvent.type(search, "nothing like this");
    expect(screen.getByText("No documents match this search.")).toBeTruthy();
    expect(rows()).toHaveLength(0);
  });
});

describe("upload", () => {
  const user = () => userEvent.setup({ applyAccept: false });

  async function fill(u: ReturnType<typeof user>, values: { product?: string; version?: string; type?: string }) {
    if (values.product !== undefined) await u.type(screen.getByLabelText("Product"), values.product);
    if (values.version !== undefined) await u.type(screen.getByLabelText("Version"), values.version);
    if (values.type !== undefined) await u.type(screen.getByLabelText("Document type"), values.type);
  }

  it("checks the form in the browser with the server's rules, without sending anything", async () => {
    const api = server({ "GET /documents": list() });
    render(<DocumentsPage active />);
    await screen.findByText(/No documents yet/);
    const u = user();

    await u.click(screen.getByRole("button", { name: "Upload" }));
    expect(screen.getByText("Choose a file to upload.")).toBeTruthy();
    expect(screen.getByText("Product is required.")).toBeTruthy();
    expect(screen.getByText("Version is required.")).toBeTruthy();
    expect(screen.getByText("Document type is required.")).toBeTruthy();

    await u.upload(screen.getByLabelText("File"), new File(["x"], "notes.docx"));
    await fill(u, { product: "documentum", version: "../16.2", type: "release notes" });
    await u.click(screen.getByRole("button", { name: "Upload" }));
    expect(screen.getByText("Only .md, .pdf, .txt files can be uploaded.")).toBeTruthy();
    expect(screen.getByText(/^Version must be 1-64 letters/)).toBeTruthy();
    expect(screen.getByText(/^Document type must be 1-64 letters/)).toBeTruthy();
    expect(screen.queryByText("Product is required.")).toBeNull();
    expect(screen.getByLabelText("Version").getAttribute("aria-invalid")).toBe("true");

    expect(api.count("POST /documents")).toBe(0);
  });

  it("uploads the file with trimmed fields, shows it at once and says indexing started", async () => {
    const uploaded: UploadedDocument = { ...PENDING, id: "id-new", filename: "New Notes.md",
      source: "documentum/16.2/release-notes/New Notes.md", ingest_job_id: "job-1" };
    const api = server({
      "GET /documents": [list(INDEXED), list({ ...uploaded, status: "processing" }, INDEXED)],
      "POST /documents": () => Response.json(uploaded, { status: 201 }),
    });
    render(<DocumentsPage active pollIntervalMs={60_000} />);
    await screen.findByText("Release Notes.pdf");
    const u = user();

    await u.upload(screen.getByLabelText("File"), new File(["# Notes"], "New Notes.md"));
    await fill(u, { product: "  documentum ", version: "16.2", type: "release-notes" });
    await u.click(screen.getByRole("button", { name: "Upload" }));

    const banner = (await screen.findByText(/Indexing has started/)).closest(".banner")!;
    expect(banner.textContent).toContain("Uploaded New Notes.md.");
    const body = api.calls.find((c) => c.key === "POST /documents")!.init!.body as FormData;
    expect([body.get("product"), body.get("version"), body.get("doc_type")]).toEqual(["documentum", "16.2", "release-notes"]);
    expect((body.get("file") as File).name).toBe("New Notes.md");
    await waitFor(() => expect(within(rowFor("New Notes.md")).getByText("Processing")).toBeTruthy());
    expect(screen.getByLabelText("Product")).toHaveProperty("value", "  documentum "); // kept for the next file
    expect(screen.getByText("Choose a file…")).toBeTruthy(); // the file is cleared
  });

  it("says when indexing couldn't start yet", async () => {
    server({
      "GET /documents": list(),
      "POST /documents": () => Response.json({ ...PENDING, ingest_job_id: null }, { status: 201 }),
    });
    render(<DocumentsPage active pollIntervalMs={60_000} />);
    await screen.findByText(/No documents yet/);
    const u = user();
    await u.upload(screen.getByLabelText("File"), new File(["x"], "waiting.md"));
    await fill(u, { product: "acme", version: "1.0", type: "notes" });
    await u.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText(/It will be indexed when indexing next runs/)).toBeTruthy();
  });

  it.each([
    [409, "A document already exists at acme/1.0/notes/waiting.md.", "Not possible."],
    [413, "The file is larger than the 50 MB limit.", "File too large."],
    [422, "This PDF can't be read; it may be damaged or password-protected.", "Please check the request."],
  ])("shows the server's reason for a %i", async (code, detail, title) => {
    server({ "GET /documents": list(), "POST /documents": status(code, { detail }) });
    render(<DocumentsPage active />);
    await screen.findByText(/No documents yet/);
    const u = user();
    await u.upload(screen.getByLabelText("File"), new File(["x"], "waiting.md"));
    await fill(u, { product: "acme", version: "1.0", type: "notes" });
    await u.click(screen.getByRole("button", { name: "Upload" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(title);
    expect(alert.textContent).toContain(detail);
  });
});

describe("indexing status", () => {
  it("checks again while a document is waiting, and stops once all are done", async () => {
    const api = server({
      "GET /documents": [list(PENDING), list({ ...PENDING, status: "processing" }), list({ ...PENDING, status: "indexed", chunk_count: 7 })],
    });
    render(<DocumentsPage active pollIntervalMs={20} />);

    await waitFor(() => expect(within(rowFor("waiting.md")).getByText("Indexed").textContent).toBe("Indexed · 7 passages"));
    const settled = api.count("GET /documents");
    await new Promise((resolve) => setTimeout(resolve, 120));
    expect(api.count("GET /documents")).toBe(settled); // nothing waiting: no more checks
    expect(settled).toBe(3);
  });

  it("doesn't keep checking while the tab is hidden", async () => {
    const api = server({ "GET /documents": list(PENDING) });
    const { rerender } = render(<DocumentsPage active pollIntervalMs={20} />);
    await screen.findByText("waiting.md");
    rerender(<DocumentsPage active={false} pollIntervalMs={20} />);
    const before = api.count("GET /documents");
    await new Promise((resolve) => setTimeout(resolve, 120));
    expect(api.count("GET /documents")).toBe(before);
  });

  it("starts indexing for waiting and failed documents", async () => {
    const api = server({
      "GET /documents": list(PENDING, FAILED),
      "POST /documents/ingest": [
        () => Response.json({ job_id: "job-1", status: "running", started_at: "2026-11-02T09:00:00Z", finished_at: null,
          report: null, error: null }, { status: 202 }),
        status(409, { detail: "An ingestion job is already running.", job_id: "job-1" }),
      ],
    });
    render(<DocumentsPage active pollIntervalMs={60_000} />);
    await screen.findByText("waiting.md");

    await userEvent.click(screen.getByRole("button", { name: "Index pending documents" }));
    expect(await screen.findByText("Indexing started.")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Index pending documents" }));
    expect(await screen.findByText(/Indexing is already running/)).toBeTruthy();
    expect(api.count("POST /documents/ingest")).toBe(2);
  });

  it("offers no indexing button when nothing is waiting", async () => {
    server({ "GET /documents": list(INDEXED) });
    render(<DocumentsPage active />);
    await screen.findByText("Release Notes.pdf");
    expect(screen.queryByRole("button", { name: "Index pending documents" })).toBeNull();
  });
});

describe("delete", () => {
  it("asks first; Cancel keeps the document", async () => {
    const api = server({ "GET /documents": list(INDEXED) });
    render(<DocumentsPage active />);
    await screen.findByText("Release Notes.pdf");

    await userEvent.click(screen.getByRole("button", { name: "Delete Release Notes.pdf" }));
    const confirm = screen.getByRole("group", { name: "Delete Release Notes.pdf" });
    expect(confirm.textContent).toContain("Delete permanently?");
    expect(document.activeElement?.textContent).toBe("Cancel"); // the safe choice has focus

    await userEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: "Delete Release Notes.pdf" })).toBeNull();
    expect(api.count("DELETE /documents/id-indexed")).toBe(0);
  });

  it("deletes after confirming", async () => {
    const api = server({ "GET /documents": list(INDEXED, FOLDER), "DELETE /documents/id-indexed": status(204) });
    render(<DocumentsPage active />);
    await screen.findByText("Release Notes.pdf");

    await userEvent.click(screen.getByRole("button", { name: "Delete Release Notes.pdf" }));
    await userEvent.click(within(screen.getByRole("group", { name: "Delete Release Notes.pdf" })).getByRole("button", { name: "Delete" }));

    expect(await screen.findByText("Deleted Release Notes.pdf.")).toBeTruthy();
    expect(screen.queryByText("documentum/16.2/release-notes/notes.pdf")).toBeNull();
    expect(rows()).toHaveLength(1);
    expect(api.count("DELETE /documents/id-indexed")).toBe(1);
  });

  it.each([
    [409, { detail: "An ingestion job is running; delete the document when it has finished.", job_id: "job-1" }, "Not possible"],
    [503, { detail: "The original file was deleted, but its passages couldn't be removed from the index. Delete the document again to finish; until then it stays listed.",
      file_deleted: true, chunks_deleted: false, record_deleted: false }, "A required service is unavailable"],
  ])("explains a refused or unfinished delete (%i) and keeps the document listed", async (code, body, title) => {
    const api = server({ "GET /documents": list(INDEXED), "DELETE /documents/id-indexed": status(code, body) });
    render(<DocumentsPage active />);
    await screen.findByText("Release Notes.pdf");

    await userEvent.click(screen.getByRole("button", { name: "Delete Release Notes.pdf" }));
    await userEvent.click(within(screen.getByRole("group", { name: "Delete Release Notes.pdf" })).getByRole("button", { name: "Delete" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(`Couldn't delete Release Notes.pdf: ${title}`);
    expect(alert.textContent).toContain(body.detail);
    expect(screen.getByText("Release Notes.pdf")).toBeTruthy();
    await waitFor(() => expect(api.count("GET /documents")).toBe(2)); // reloaded to show what is left
  });
});
