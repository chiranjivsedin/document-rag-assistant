import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App, TABS } from "./App";

beforeEach(() => {
  const ok = { status: "ok", llm_model: "gemma3:4b", embedding_model: "nomic-embed-text", problems: [] };
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(ok)));
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
    await screen.findByText(/Services OK/); // the health banner is part of the shell
  });

  it("switches tabs through the URL hash", async () => {
    render(<App />);
    await userEvent.click(screen.getByRole("tab", { name: "Ingestion" }));
    expect(window.location.hash).toBe("#ingestion");
    expect(await screen.findByRole("heading", { name: "Ingestion" })).toBeTruthy();
    expect(selectedTab()?.textContent).toBe("Ingestion");
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

  it("marks unbuilt sections as placeholders", () => {
    render(<App />);
    expect(screen.getByText(/Not built yet/).textContent).toContain("6B");
  });
});
