import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { HealthBanner } from "./HealthBanner";

const OK = { status: "ok", llm_model: "gemma3:4b", embedding_model: "nomic-embed-text", problems: [] };
const DOWN = {
  status: "unavailable",
  llm_model: "gemma3:4b",
  embedding_model: "nomic-embed-text",
  problems: [
    "Can't reach Ollama at http://127.0.0.1:11434. Is it running? Start Ollama, or check OLLAMA_BASE_URL.",
    "Can't connect to the database at 127.0.0.1:5433 (connection timeout expired).",
  ],
};

function stubFetch(...replies: Array<() => Promise<Response>>) {
  const fetch = vi.fn();
  for (const reply of replies) fetch.mockImplementationOnce(reply);
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("HealthBanner", () => {
  it("shows 'checking' until the answer arrives, then the models", async () => {
    stubFetch(async () => Response.json(OK));
    render(<HealthBanner />);
    expect(screen.getByRole("status").textContent).toContain("Checking");
    expect((await screen.findByText(/Services OK/)).textContent).toContain("gemma3:4b");
  });

  it("lists each problem when services are unavailable", async () => {
    stubFetch(async () => Response.json(DOWN, { status: 503 }));
    render(<HealthBanner />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Services unavailable");
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(DOWN.problems);
  });

  it("says the API is unreachable when the request fails", async () => {
    stubFetch(async () => Promise.reject(new TypeError("Failed to fetch")));
    render(<HealthBanner />);
    expect((await screen.findByRole("alert")).textContent).toContain("Can't reach the API");
  });

  it("checks again on request", async () => {
    const fetch = stubFetch(
      async () => Response.json(DOWN, { status: 503 }),
      async () => Response.json(OK),
    );
    render(<HealthBanner />);
    await screen.findByText(/Services unavailable/);
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    await screen.findByText(/Services OK/);
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
