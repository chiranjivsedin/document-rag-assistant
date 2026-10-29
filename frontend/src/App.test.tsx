import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App, TABS } from "./App";

const OK = { status: "ok", llm_model: "gemma3:4b", embedding_model: "nomic-embed-text", problems: [] };
const ANSWER = { answer: "Kept answer [1].", no_answer: false, sources: [], model: "gemma3:4b", elapsed_ms: 1000 };

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => Response.json(url === "/health" ? OK : ANSWER)),
  );
});

afterEach(() => {
  delete document.documentElement.dataset.theme;
  localStorage.clear();
});

function selectedTab() {
  return screen.getAllByRole("tab").find((tab) => tab.getAttribute("aria-selected") === "true");
}

async function askQuestion(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(screen.getByLabelText("Question"), text);
  await user.click(screen.getByRole("button", { name: "Ask" }));
  await screen.findAllByText(/Kept answer/);
}

describe("App shell", () => {
  it("shows one tab per section and opens Chat by default", async () => {
    render(<App />);
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(TABS.map((tab) => tab.label));
    expect(selectedTab()?.textContent).toBe("Chat");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Chat");
    expect(screen.getByRole("tabpanel").textContent).toContain("What would you like to know?");
    expect(screen.getByRole("form", { name: "Ask a question" })).toBeTruthy();
    expect(await screen.findByText("All services running")).toBeTruthy(); // status in the sidebar
    expect(screen.getByTitle("Chat model answering your questions").textContent).toBe("gemma3:4b");
  });

  it("switches tabs through the URL hash", async () => {
    render(<App />);
    await userEvent.click(screen.getByRole("tab", { name: "Ingestion" }));
    expect(window.location.hash).toBe("#ingestion");
    expect(await screen.findByRole("heading", { level: 1, name: "Ingestion" })).toBeTruthy();
    expect(selectedTab()?.textContent).toBe("Ingestion");
    expect(screen.getAllByRole("tabpanel")).toHaveLength(1); // the hidden panels don't count
  });

  it("opens the tab named in the hash on load", () => {
    window.location.hash = "#documents";
    render(<App />);
    expect(selectedTab()?.textContent).toBe("Documents");
  });

  it("falls back to Chat for an unknown hash", () => {
    window.location.hash = "#nope";
    render(<App />);
    expect(selectedTab()?.textContent).toBe("Chat");
  });

  it("marks unbuilt sections as placeholders", async () => {
    render(<App />);
    await userEvent.click(screen.getByRole("tab", { name: "Documents" }));
    expect((await screen.findByText(/Not built yet/)).textContent).toContain("6E");
  });

  it("shows a notice listing each problem when services are down", async () => {
    const down = { ...OK, status: "unavailable", problems: ["Can't reach Ollama.", "Can't connect to the database."] };
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(down, { status: 503 })));
    render(<App />);
    const alert = await screen.findByRole("alert");
    expect(within(alert).getAllByRole("listitem").map((li) => li.textContent)).toEqual(down.problems);
    expect(screen.getByText("Services unavailable")).toBeTruthy(); // sidebar status
    expect(screen.queryByTitle("Chat model answering your questions")).toBeNull();
  });
});

describe("App: chat across the shell", () => {
  it("Release notes hands a version-scoped question to Chat without sending it", async () => {
    const fetch = vi.mocked(globalThis.fetch);
    render(<App />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Release notes" }));
    const version = screen.getByRole("textbox", { name: "Version" });
    await user.clear(version);
    await user.type(version, "16.2");
    await user.click(screen.getByRole("button", { name: /Which features are deprecated in version 16\.2\?/ }));
    expect(window.location.hash).toBe("#chat");
    expect(await screen.findByRole("heading", { level: 1, name: "Chat" })).toBeTruthy();
    expect((screen.getByLabelText("Question") as HTMLTextAreaElement).value).toBe(
      "Which features are deprecated in version 16.2?",
    );
    expect((screen.getByLabelText("Version (optional)") as HTMLInputElement).value).toBe("16.2");
    expect(fetch.mock.calls.filter(([url]) => url === "/chat")).toHaveLength(0);
  });

  it("keeps the chat history when switching tabs and back", async () => {
    render(<App />);
    const user = userEvent.setup();
    await askQuestion(user, "Q?");
    await user.click(screen.getByRole("tab", { name: "Release notes" }));
    await user.click(screen.getByRole("tab", { name: "Chat" }));
    expect(screen.getByText(/Kept answer/)).toBeTruthy();
  });

  it("lists this session's questions in the sidebar and jumps back to one", async () => {
    render(<App />);
    const user = userEvent.setup();
    await askQuestion(user, "First question?");
    await user.click(screen.getByRole("tab", { name: "Ingestion" }));
    await user.click(screen.getByRole("button", { name: "First question?" }));
    expect(window.location.hash).toBe("#chat");
  });

  it("New chat starts an empty conversation and clears the session list", async () => {
    render(<App />);
    const user = userEvent.setup();
    await askQuestion(user, "Old question?");
    expect(screen.getByText("This session")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.getByText("What would you like to know?")).toBeTruthy();
    expect(screen.queryByText(/Kept answer/)).toBeNull();
    expect(screen.queryByText("This session")).toBeNull();
  });
});

describe("App: theme and menu", () => {
  it("switches the theme and remembers it in this browser", async () => {
    render(<App />);
    const user = userEvent.setup();
    const themes = screen.getByRole("radiogroup", { name: "Theme" });
    expect(within(themes).getByRole("radio", { name: "System" }).getAttribute("aria-checked")).toBe("true");
    await user.click(within(themes).getByRole("radio", { name: "Dark" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("rag-assistant-theme")).toBe("dark");
    await user.click(within(themes).getByRole("radio", { name: "System" }));
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(localStorage.getItem("rag-assistant-theme")).toBeNull();
  });

  it("opens the menu on small screens and closes it with Escape", async () => {
    render(<App />);
    const user = userEvent.setup();
    const open = screen.getByRole("button", { name: "Open menu" });
    await user.click(open);
    expect(open.getAttribute("aria-expanded")).toBe("true");
    await user.keyboard("{Escape}");
    expect(open.getAttribute("aria-expanded")).toBe("false");
  });
});
