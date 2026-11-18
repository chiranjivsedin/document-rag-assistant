import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TagCategory } from "../../api/types";
import { TagsPage } from "./TagsPage";

const KEY = "the-admin-key";

const LISTS: TagCategory[] = [
  { key: "product", label: "Product", builtin: true, multiple: false, values: [
    { id: 1, code: "content-management", label: "Content Management", active: true },
  ] },
  { key: "version", label: "Version", builtin: true, multiple: false, values: [
    { id: 10, code: "16.2", label: "16.2", active: false },
    { id: 11, code: "24.4", label: "24.4", active: true },
  ] },
  { key: "module", label: "Module", builtin: false, multiple: true, values: [] },
];

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>;

/** fetch stand-in routed by "METHOD path"; GET /tags answers with LISTS unless a test routes it. */
function server(given: Record<string, Handler | Handler[]> = {}) {
  const routes: Record<string, Handler | Handler[]> = { "GET /tags": () => Response.json({ categories: LISTS }), ...given };
  const calls: Array<{ key: string; url: string; init?: RequestInit }> = [];
  const counts: Record<string, number> = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      calls.push({ key, url, init });
      const route = routes[key];
      if (!route) throw new Error(`unexpected request: ${key}`);
      const call = (counts[key] = (counts[key] ?? 0) + 1);
      const handlers = Array.isArray(route) ? route : [route];
      return handlers[Math.min(call, handlers.length) - 1](init);
    }),
  );
  const sent = (key: string) => calls.filter((c) => c.key === key);
  return { calls, sent };
}

const json = (status: number, body: unknown): Handler => () => Response.json(body, { status });
const noContent: Handler = () => new Response(null, { status: 204 });

function headerOf(call: { init?: RequestInit }, name: string): string | null {
  return new Headers(call.init?.headers).get(name);
}

async function unlock(user: ReturnType<typeof userEvent.setup>, key = KEY) {
  await user.type(screen.getByLabelText("Admin key"), key);
  await user.click(screen.getByRole("button", { name: "Unlock editing" }));
}

function section(label: string) {
  return screen.getByRole("region", { name: label });
}

beforeEach(() => sessionStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("TagsPage", () => {
  it("lists every category and value, retired ones marked, and offers no changes without the key", async () => {
    server();
    render(<TagsPage />);
    await screen.findByText("Content Management");

    const versions = within(section("Version")).getAllByRole("row").slice(1).map((row) => row.textContent);
    expect(versions).toEqual(["16.216.2Retired", "24.424.4Active"]);
    expect(within(section("Module")).getByText("No values yet.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Retire|Restore|Delete|Rename/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add category" })).toBeNull();
    expect(screen.getByLabelText("Admin key").getAttribute("type")).toBe("password");
  });

  it("asks for the key once, keeps it in this tab's session storage only, never in the URL", async () => {
    const api = server({ "PATCH /tags/values/11": json(200, { id: 11, code: "24.4", label: "24.4", active: false }) });
    const { unmount } = render(<TagsPage />);
    await screen.findByText("Content Management");
    const user = userEvent.setup();
    await unlock(user);

    expect(sessionStorage.getItem("rag.adminKey")).toBe(KEY);
    expect(localStorage.getItem("rag.adminKey")).toBeNull();
    expect(screen.queryByLabelText("Admin key")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Retire 24.4" }));
    await screen.findByText('Retired "24.4".');

    const [patch] = api.sent("PATCH /tags/values/11");
    expect(headerOf(patch, "X-Admin-Key")).toBe(KEY);
    expect(JSON.parse(patch.init!.body as string)).toEqual({ active: false });
    expect(api.calls.every((call) => !call.url.includes(KEY))).toBe(true);
    expect(window.location.href).not.toContain(KEY);
    expect(headerOf(api.sent("GET /tags")[0], "X-Admin-Key")).toBeNull(); // reading needs no key
    await waitFor(() => expect(api.sent("GET /tags")).toHaveLength(2)); // the lists are loaded again

    unmount();
    render(<TagsPage />); // the same tab later: not asked again
    await screen.findAllByText("Content Management");
    expect(screen.queryByLabelText("Admin key")).toBeNull();
    expect(screen.getByRole("button", { name: "Restore 16.2" })).toBeTruthy();
  });

  it("adds a category, checking its key and label in the browser first", async () => {
    const api = server({ "POST /tags/categories": json(201, { key: "audience", label: "Audience", builtin: false, multiple: true, values: [] }) });
    sessionStorage.setItem("rag.adminKey", KEY);
    render(<TagsPage />);
    await screen.findByText("Content Management");
    const user = userEvent.setup();
    const form = within(section("Add a category"));

    await user.type(form.getByLabelText("Key"), "Audience");
    await user.click(form.getByRole("button", { name: "Add category" }));
    expect(form.getByText(/The key must be 1-32 lowercase letters/)).toBeTruthy();
    expect(form.getByText("The label must be 1-64 characters.")).toBeTruthy();
    expect(api.sent("POST /tags/categories")).toHaveLength(0);

    await user.clear(form.getByLabelText("Key"));
    await user.type(form.getByLabelText("Key"), "audience");
    await user.type(form.getByLabelText("Category label"), "  Audience ");
    await user.click(form.getByRole("checkbox", { name: "Several values per document" }));
    await user.click(form.getByRole("button", { name: "Add category" }));

    await screen.findByText('Added the category "Audience".');
    const [post] = api.sent("POST /tags/categories");
    expect(JSON.parse(post.init!.body as string)).toEqual({ key: "audience", label: "Audience", multiple: true });
    expect(headerOf(post, "X-Admin-Key")).toBe(KEY);
  });

  it("adds a value, renames a label, restores and deletes values", async () => {
    const api = server({
      "POST /tags/categories/module/values": json(201, { id: 50, code: "workflow", label: "Workflow", active: true }),
      "PATCH /tags/values/11": json(200, { id: 11, code: "24.4", label: "Release 24.4", active: true }),
      "PATCH /tags/values/10": json(200, { id: 10, code: "16.2", label: "16.2", active: true }),
      "DELETE /tags/values/1": json(409, { detail: "'Content Management' is in use, so it can only be retired." }),
      "DELETE /tags/values/10": noContent,
    });
    sessionStorage.setItem("rag.adminKey", KEY);
    render(<TagsPage />);
    await screen.findByText("Content Management");
    const user = userEvent.setup();

    const addToModule = within(screen.getByRole("form", { name: "Add a value to Module" }));
    await user.type(addToModule.getByLabelText("Code"), "../x");
    await user.click(addToModule.getByRole("button", { name: "Add value" }));
    expect(addToModule.getByText(/^The code must be 1-64 letters/)).toBeTruthy();
    await user.clear(addToModule.getByLabelText("Code"));
    await user.type(addToModule.getByLabelText("Code"), "workflow");
    await user.type(addToModule.getByLabelText("Label"), "Workflow");
    await user.click(addToModule.getByRole("button", { name: "Add value" }));
    await screen.findByText('Added "Workflow" to Module.');
    expect(JSON.parse(api.sent("POST /tags/categories/module/values")[0].init!.body as string)).toEqual({
      code: "workflow", label: "Workflow" });

    await user.click(screen.getByRole("button", { name: "Rename 24.4" }));
    const box = screen.getByLabelText("New label for 24.4");
    await user.clear(box);
    await user.type(box, "Release   24.4");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText('Renamed to "Release 24.4".');
    expect(JSON.parse(api.sent("PATCH /tags/values/11")[0].init!.body as string)).toEqual({ label: "Release 24.4" });

    await user.click(screen.getByRole("button", { name: "Restore 16.2" }));
    await screen.findByText('Restored "16.2".');
    expect(JSON.parse(api.sent("PATCH /tags/values/10")[0].init!.body as string)).toEqual({ active: true });

    await user.click(screen.getByRole("button", { name: "Delete Content Management" }));
    expect((await screen.findByRole("alert")).textContent).toContain("is in use, so it can only be retired");
    await user.click(screen.getByRole("button", { name: "Delete 16.2" }));
    await screen.findByText('Deleted "16.2" from Version.');
  });

  it("forgets a wrong key and asks again", async () => {
    server({ "PATCH /tags/values/11": json(401, { detail: "Missing or wrong admin key." }) });
    sessionStorage.setItem("rag.adminKey", "stale key");
    render(<TagsPage />);
    await screen.findByText("Content Management");
    await userEvent.click(screen.getByRole("button", { name: "Retire 24.4" }));

    expect((await screen.findByRole("alert")).textContent).toContain("Enter the admin key again.");
    expect(sessionStorage.getItem("rag.adminKey")).toBeNull();
    expect(screen.getByLabelText("Admin key")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retire 24.4" })).toBeNull();
  });

  it("says when admin is disabled on the server", async () => {
    server({ "PATCH /tags/values/11": json(403, { detail: "Admin is disabled on this server." }) });
    render(<TagsPage />);
    await screen.findByText("Content Management");
    const user = userEvent.setup();
    await unlock(user);
    await user.click(screen.getByRole("button", { name: "Retire 24.4" }));

    expect((await screen.findByText(/Admin is disabled on this server/)).textContent).toContain("ADMIN_KEY");
    expect(screen.queryByRole("button", { name: "Retire 24.4" })).toBeNull();
    expect(screen.queryByLabelText("Admin key")).toBeNull();
  });
});
