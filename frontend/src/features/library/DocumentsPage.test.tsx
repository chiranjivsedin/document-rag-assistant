import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DocumentRecord, TagCategory, UploadedDocument } from "../../api/types";
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
    tags: [],
    unlisted: [],
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

// The master lists the screen loads (GET /tags): the three built-ins, with a retired version, plus an extra
// single-value category "module" and a multi-value one "audience".
const TAGS: TagCategory[] = [
  { key: "product", label: "Product", builtin: true, multiple: false, values: [
    { id: 1, code: "content-management", label: "Content Management", active: true },
    { id: 2, code: "documentum", label: "Documentum", active: true },
    { id: 3, code: "acme", label: "Acme", active: true },
  ] },
  { key: "version", label: "Version", builtin: true, multiple: false, values: [
    { id: 10, code: "16.2", label: "16.2", active: true },
    { id: 11, code: "1.0", label: "1.0", active: true },
    { id: 12, code: "20.2", label: "20.2", active: false },
  ] },
  { key: "doc_type", label: "Document type", builtin: true, multiple: false, values: [
    { id: 20, code: "release-notes", label: "Release notes", active: true },
    { id: 21, code: "notes", label: "Notes", active: true },
  ] },
  { key: "module", label: "Module", builtin: false, multiple: false, values: [
    { id: 30, code: "workflow", label: "Workflow", active: true },
    { id: 31, code: "search", label: "Search", active: true },
    { id: 32, code: "old", label: "Old module", active: false },
  ] },
  { key: "audience", label: "Audience", builtin: false, multiple: true, values: [
    { id: 40, code: "admins", label: "Administrators", active: true },
    { id: 41, code: "developers", label: "Developers", active: true },
  ] },
];
const tagLists: Handler = () => Response.json({ categories: TAGS });

/**
 * fetch stand-in routed by "METHOD path". A list of handlers answers successive calls in turn (the last one repeats).
 * Unrouted calls fail the test.
 */
function server(given: Record<string, Handler | Handler[]>) {
  const routes: Record<string, Handler | Handler[]> = { "GET /tags": tagLists, ...given };
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
    expect(within(folder).getByTitle("Product").textContent).toBe("Acme"); // no version or type: only its product shows
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
    await screen.findAllByRole("option", { name: "Content Management" }); // the master lists have loaded
    if (values.product !== undefined) await u.selectOptions(screen.getByLabelText("Product"), values.product);
    if (values.version !== undefined) await u.selectOptions(screen.getByLabelText("Version"), values.version);
    if (values.type !== undefined) await u.selectOptions(screen.getByLabelText("Document type"), values.type);
  }

  function optionsOf(label: string) {
    return within(screen.getByLabelText(label)).getAllByRole("option").map((o) => o.textContent);
  }

  it("offers only the master lists' active values, as dropdowns: nothing is typed", async () => {
    server({ "GET /documents": list() });
    render(<DocumentsPage active />);
    await screen.findAllByRole("option", { name: "Content Management" });

    expect(optionsOf("Product")).toEqual(["Choose…", "Content Management", "Documentum", "Acme"]);
    expect(optionsOf("Version")).toEqual(["Choose…", "16.2", "1.0"]); // 20.2 is retired
    expect(optionsOf("Document type")).toEqual(["Choose…", "Release notes", "Notes"]);
    expect(optionsOf("Module")).toEqual(["None", "Workflow", "Search"]); // single value: a dropdown
    const audience = screen.getByRole("group", { name: "Audience" }); // several values: check boxes
    expect(within(audience).getAllByRole("checkbox")).toHaveLength(2);

    const form = screen.getByRole("button", { name: "Upload" }).closest("form")!;
    expect(within(form).queryAllByRole("textbox")).toHaveLength(0); // no free-text field left
    expect(form.querySelector("datalist")).toBeNull();
  });

  it("sends the chosen codes and tag ids", async () => {
    const uploaded: UploadedDocument = { ...PENDING, id: "id-new", ingest_job_id: null };
    const api = server({
      "GET /documents": list(),
      "POST /documents": () => Response.json(uploaded, { status: 201 }),
    });
    render(<DocumentsPage active pollIntervalMs={60_000} />);
    await screen.findByText(/No documents yet/);
    const u = user();
    await u.upload(screen.getByLabelText("File"), new File(["x"], "notes.md"));
    await fill(u, { product: "Content Management", version: "16.2", type: "Release notes" });
    await u.selectOptions(screen.getByLabelText("Module"), "Search");
    await u.click(screen.getByRole("checkbox", { name: "Administrators" }));
    await u.click(screen.getByRole("checkbox", { name: "Developers" }));
    await u.click(screen.getByRole("checkbox", { name: "Administrators" })); // and off again
    await u.click(screen.getByRole("button", { name: "Upload" }));

    await screen.findByText(/It will be indexed/);
    const body = api.calls.find((c) => c.key === "POST /documents")!.init!.body as FormData;
    expect([body.get("product"), body.get("version"), body.get("doc_type")]).toEqual([
      "content-management", "16.2", "release-notes"]);
    expect(body.getAll("tag")).toEqual(["31", "41"]);
  });

  it("says when the master lists can't be loaded, and can try again", async () => {
    server({
      "GET /documents": list(),
      "GET /tags": [status(503, { detail: "Ollama or the database stopped responding. Check GET /health." }), tagLists],
    });
    render(<DocumentsPage active />);
    expect(await screen.findByText(/Couldn't load the master lists/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findAllByRole("option", { name: "Content Management" })).toBeTruthy();
    expect(screen.queryByText(/Couldn't load the master lists/)).toBeNull();
  });

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
    await fill(u, { product: "Documentum", type: "Release notes" });
    await u.click(screen.getByRole("button", { name: "Upload" }));
    expect(screen.getByText("Only .md, .pdf, .txt files can be uploaded.")).toBeTruthy();
    expect(screen.getByText("Version is required.")).toBeTruthy();
    expect(screen.queryByText("Document type is required.")).toBeNull();
    expect(screen.queryByText("Product is required.")).toBeNull();
    expect(screen.getByLabelText("Version").getAttribute("aria-invalid")).toBe("true");

    expect(api.count("POST /documents")).toBe(0);
  });

  it("uploads the file, shows it at once and says indexing started", async () => {
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
    await fill(u, { product: "Documentum", version: "16.2", type: "Release notes" });
    await u.click(screen.getByRole("button", { name: "Upload" }));

    const banner = (await screen.findByText(/Indexing has started/)).closest(".banner")!;
    expect(banner.textContent).toContain("Uploaded New Notes.md.");
    const body = api.calls.find((c) => c.key === "POST /documents")!.init!.body as FormData;
    expect([body.get("product"), body.get("version"), body.get("doc_type")]).toEqual(["documentum", "16.2", "release-notes"]);
    expect((body.get("file") as File).name).toBe("New Notes.md");
    await waitFor(() => expect(within(rowFor("New Notes.md")).getByText("Processing")).toBeTruthy());
    expect(screen.getByLabelText("Product")).toHaveProperty("value", "documentum"); // kept for the next file
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
    await fill(u, { product: "Acme", version: "1.0", type: "Notes" });
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
    await fill(u, { product: "Acme", version: "1.0", type: "Notes" });
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

describe("tags", () => {
  const TAGGED = doc({
    id: "id-tagged", filename: "tagged.md", source: "content-management/16.2/release-notes/tagged.md",
    product: "content-management", version: "16.2", doc_type: "release-notes",
    tags: [
      { id: 30, category: "module", code: "workflow", label: "Workflow", active: true },
      { id: 32, category: "module", code: "old", label: "Old module", active: false },
      { id: 41, category: "audience", code: "developers", label: "Developers", active: true },
    ],
  });
  const RETIRED_VERSION = doc({ id: "id-old", filename: "old.md", source: "acme/20.2/notes/old.md", product: "acme",
    version: "20.2", doc_type: "notes" });
  const UNLISTED = doc({ id: "id-hand", filename: "by-hand.md", source: "documentum/23.4/guides/by-hand.md",
    product: "documentum", version: "23.4", doc_type: "guides", origin: "folder", unlisted: ["version", "doc_type"] });

  function chips(name: string) {
    return within(within(rowFor(name)).getByText(name).closest("tr")!.querySelector(".tag-list") as HTMLElement)
      .getAllByTitle(/.*/)
      .map((chip) => `${chip.getAttribute("title")}: ${chip.textContent}`);
  }

  it("shows each document's tags by label, marking retired values and values not in the master list", async () => {
    server({ "GET /documents": list(TAGGED, RETIRED_VERSION, UNLISTED) });
    render(<DocumentsPage active />);
    await screen.findByText("tagged.md");
    await screen.findAllByRole("option", { name: "Content Management" });

    expect(chips("tagged.md")).toEqual([
      "Product: Content Management", "Version: 16.2", "Document type: Release notes",
      "Module: Workflow", "Module: Old module · retired", "Audience: Developers"]);
    expect(chips("old.md")).toEqual(["Product: Acme", "Version: 20.2 · retired", "Document type: Notes"]);
    expect(chips("by-hand.md")).toEqual([
      "Product: Documentum", "Version: 23.4 · not in master list", "Document type: guides · not in master list"]);
  });

  it("filters the list by any category, built-in or extra", async () => {
    server({ "GET /documents": list(TAGGED, RETIRED_VERSION, UNLISTED, INDEXED) });
    render(<DocumentsPage active />);
    await screen.findByText("tagged.md");
    await screen.findAllByRole("option", { name: "Content Management" });
    const names = () => rows().map((r) => within(r).getByRole("strong").textContent);

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Filter by Module" }), "Workflow");
    expect(names()).toEqual(["tagged.md"]);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Filter by Module" }), "All: Module");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Filter by Version" }), "20.2 (retired)");
    expect(names()).toEqual(["old.md"]); // a retired value still filters
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Filter by Product" }), "Documentum");
    expect(screen.getByText("No documents match this search.")).toBeTruthy();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Filter by Version" }), "16.2");
    expect(names()).toEqual(["Release Notes.pdf"]);
  });

  it("edits a document's extra tags; retired ones it has may stay, and none needs the admin key", async () => {
    const saved: DocumentRecord = { ...TAGGED, tags: [
      { id: 31, category: "module", code: "search", label: "Search", active: true },
      { id: 41, category: "audience", code: "developers", label: "Developers", active: true },
      { id: 40, category: "audience", code: "admins", label: "Administrators", active: true },
    ] };
    const api = server({
      "GET /documents": list(TAGGED),
      "PUT /documents/id-tagged/tags": () => Response.json(saved),
    });
    render(<DocumentsPage active />);
    await screen.findByText("tagged.md");
    await screen.findAllByRole("option", { name: "Content Management" });
    const u = userEvent.setup();

    await u.click(screen.getByRole("button", { name: "Edit tags of tagged.md" }));
    const editor = screen.getByRole("group", { name: "Tags of tagged.md" });
    // The single-value Module offers active values plus the retired one the document has.
    expect(within(editor).getAllByRole("combobox")).toHaveLength(1);
    expect(within(within(editor).getByLabelText("Module")).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "None", "Workflow", "Search", "Old module (retired)"]);
    await u.selectOptions(within(editor).getByLabelText("Module"), "Search");
    await u.click(within(editor).getByRole("checkbox", { name: "Administrators" }));
    await u.click(within(editor).getByRole("button", { name: "Save tags" }));

    expect(await screen.findByText("Saved the tags of tagged.md.")).toBeTruthy();
    const put = api.calls.find((c) => c.key === "PUT /documents/id-tagged/tags")!;
    expect(JSON.parse(put.init!.body as string)).toEqual({ value_ids: [31, 41, 40] });
    expect(new Headers(put.init!.headers).has("X-Admin-Key")).toBe(false);
    expect(screen.queryByRole("group", { name: "Tags of tagged.md" })).toBeNull();
    expect(chips("tagged.md")).toContain("Module: Search");
  });

  it("keeps the editor open with the server's reason when saving fails", async () => {
    server({
      "GET /documents": list(TAGGED),
      "PUT /documents/id-tagged/tags": status(422, { detail: "Module takes one value only." }),
    });
    render(<DocumentsPage active />);
    await screen.findByText("tagged.md");
    await screen.findAllByRole("option", { name: "Content Management" });
    await userEvent.click(screen.getByRole("button", { name: "Edit tags of tagged.md" }));
    await userEvent.click(screen.getByRole("button", { name: "Save tags" }));
    const editor = screen.getByRole("group", { name: "Tags of tagged.md" });
    expect((await within(editor).findByRole("alert")).textContent).toContain("Module takes one value only.");
  });
});
