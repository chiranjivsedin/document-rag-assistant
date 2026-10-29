import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { HealthAlert, HealthStatus, useHealth } from "./Health";

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

/** The status indicator and the problem notice, wired to one useHealth() as in the app shell. */
function Harness() {
  const { state, recheck } = useHealth();
  return (
    <>
      <HealthStatus state={state} onRecheck={recheck} />
      <HealthAlert state={state} onRecheck={recheck} />
    </>
  );
}

function stubFetch(...replies: Array<() => Promise<Response>>) {
  const fetch = vi.fn();
  for (const reply of replies) fetch.mockImplementationOnce(reply);
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("health status", () => {
  it("shows 'checking' until the answer arrives, then the models", async () => {
    stubFetch(async () => Response.json(OK));
    render(<Harness />);
    expect(screen.getByRole("status").textContent).toContain("Checking services");
    expect(await screen.findByText("All services running")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("Chat model gemma3:4b, embedding model nomic-embed-text");
    expect(screen.queryByRole("alert")).toBeNull(); // no notice while all is well
  });

  it("lists each problem in the notice when services are unavailable", async () => {
    stubFetch(async () => Response.json(DOWN, { status: 503 }));
    render(<Harness />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Services unavailable.");
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(DOWN.problems);
    expect(screen.getByRole("status").textContent).toContain("2 problems");
  });

  it("says the API is unreachable when the request fails", async () => {
    stubFetch(async () => Promise.reject(new TypeError("Failed to fetch")));
    render(<Harness />);
    expect((await screen.findByRole("alert")).textContent).toContain("Can't reach the API");
    expect(screen.getByRole("status").textContent).toContain("API unreachable");
  });

  it("checks again on request, from the notice or the status", async () => {
    const fetch = stubFetch(
      async () => Response.json(DOWN, { status: 503 }),
      async () => Response.json(OK),
      async () => Response.json(OK),
    );
    render(<Harness />);
    const user = userEvent.setup();
    const alert = await screen.findByRole("alert");
    await user.click(within(alert).getByRole("button", { name: "Check again" }));
    await screen.findByText("All services running");
    expect(screen.queryByRole("alert")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Check again" })); // the status's re-check button
    await screen.findByText("All services running");
    expect(fetch).toHaveBeenCalledTimes(3);
  });
});
