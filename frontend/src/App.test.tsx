import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App, TABS } from "./App";

const OK = { status: "ok", llm_model: "gemma3:4b", embedding_model: "nomic-embed-text", problems: [] };
const ANSWER = { answer: "Kept answer [1].", no_answer: false, sources: [], model: "gemma3:4b", elapsed_ms: 1000 };

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => Response.json(url === "/health" ? OK : ANSWER)),
  );
});

function selectedTab() {
  return screen.getAllByRole("tab").find((tab) => tab.getAttribute("aria-selected") === "true");
}

describe("App shell", () => {
  it("shows one tab per section and opens Chat by default", async () => {
    render(<App />);
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(TABS.map((tab) => tab.label));
    expect(selectedTab()?.textContent).toBe("Chat");
    expect(screen.getByRole("tabpanel").textContent).toContain("Ask questions");
    expect(screen.getByRole("form", { name: "Ask a question" })).toBeTruthy();
    await screen.findByText(/Services OK/); // the health banner is part of the shell
  });

  it("switches tabs through the URL hash", async () => {
    render(<App />);
    await userEvent.click(screen.getByRole("tab", { name: "Ingestion" }));
    expect(window.location.hash).toBe("#ingestion");
    expect(await screen.findByRole("heading", { name: "Ingestion" })).toBeTruthy();
    expect(selectedTab()?.textContent).toBe("Ingestion");
    expect(screen.getAllByRole("tabpanel")).toHaveLength(1); // the hidden chat panel doesn't count
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

  it("keeps the chat history when switching tabs and back", async () => {
    render(<App />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Question"), "Q?");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByText(/Kept answer/);
    await user.click(screen.getByRole("tab", { name: "Release notes" }));
    await user.click(screen.getByRole("tab", { name: "Chat" }));
    expect(screen.getByText(/Kept answer/)).toBeTruthy();
  });
});
