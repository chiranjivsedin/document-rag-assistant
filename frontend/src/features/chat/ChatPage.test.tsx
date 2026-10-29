import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ChatResponse } from "../../api/types";
import { ChatPage } from "./ChatPage";

const NO_ANSWER = "I don't know based on the available documents.";

const ANSWERED: ChatResponse = {
  answer: "Classic Desktop is deprecated [1]. Use Acme Web instead [1][2]. See also [7].",
  no_answer: false,
  sources: [
    {
      number: 1,
      source: "acme-dms/1.0/deprecated-features/deprecated-features.md",
      product: "acme-dms",
      version: "1.0",
      doc_type: "deprecated-features",
      page: null,
      distance: 0.2134,
      excerpt: "Classic Desktop client: deprecated in 1.0.",
    },
    {
      number: 2,
      source: "acme-dms/1.0/release-notes/notes.pdf",
      product: "acme-dms",
      version: "1.0",
      doc_type: "release-notes",
      page: 3,
      distance: 0.4,
      excerpt: "Acme Web replaces the desktop client.",
    },
    {
      number: 3,
      source: "acme-dms/1.0/release-notes/other.md",
      product: "acme-dms",
      version: "1.0",
      doc_type: "release-notes",
      page: null,
      distance: 0.55,
      excerpt: "Unrelated passage.",
    },
  ],
  model: "gemma3:4b",
  elapsed_ms: 18_400,
};

/** fetch stand-in answering POST /chat with `body` and recording each request body. */
function answer(body: unknown, status = 200) {
  const requests: unknown[] = [];
  const fetch = vi.fn(async (_url: string, init: RequestInit) => {
    requests.push(JSON.parse(init.body as string));
    return Response.json(body, { status });
  });
  vi.stubGlobal("fetch", fetch);
  return { fetch, requests };
}

/** fetch stand-in that never answers until the request is aborted. */
function neverAnswer() {
  const signals: AbortSignal[] = [];
  const fetch = vi.fn(
    (_url: string, init: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        signals.push(init.signal!);
        init.signal!.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      }),
  );
  vi.stubGlobal("fetch", fetch);
  return { fetch, signals };
}

async function ask(question: string, options: { product?: string; version?: string; k?: string } = {}) {
  const user = userEvent.setup();
  if (options.product) await user.type(screen.getByLabelText("Product (optional)"), options.product);
  if (options.version) await user.type(screen.getByLabelText("Version (optional)"), options.version);
  if (options.k !== undefined) {
    await user.clear(screen.getByLabelText("Top-k"));
    await user.type(screen.getByLabelText("Top-k"), options.k);
  }
  await user.type(screen.getByLabelText("Question"), question);
  await user.click(screen.getByRole("button", { name: "Ask" }));
  return user;
}

describe("ChatPage: asking", () => {
  it("sends the question with trimmed filters and k, and shows the answer with model and time", async () => {
    const { fetch, requests } = answer(ANSWERED);
    render(<ChatPage />);
    await ask("  What is deprecated?  ", { product: " acme-dms ", version: "1.0", k: "3" });

    expect(await screen.findByText(/See also/)).toBeTruthy();
    expect(fetch).toHaveBeenCalledWith("/chat", expect.objectContaining({ method: "POST" }));
    expect(requests).toEqual([{ question: "What is deprecated?", k: 3, product: "acme-dms", version: "1.0" }]);
    expect(screen.getByText("gemma3:4b · 18.4 s")).toBeTruthy();
    expect(screen.getByText("product acme-dms · version 1.0 · top 3")).toBeTruthy();
    expect((screen.getByLabelText("Question") as HTMLTextAreaElement).value).toBe(""); // ready for the next question
  });

  it("leaves out empty filters (they would mean 'no filter' anyway)", async () => {
    const { requests } = answer(ANSWERED);
    render(<ChatPage />);
    await ask("Q?", { product: "   " });
    await screen.findByText(/See also/);
    expect(requests).toEqual([{ question: "Q?", k: 5 }]);
  });

  it("keeps the Ask button disabled for a blank question", async () => {
    answer(ANSWERED);
    render(<ChatPage />);
    await userEvent.type(screen.getByLabelText("Question"), "   ");
    expect((screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("rejects a top-k outside 1–20 without calling the API", async () => {
    const { fetch } = answer(ANSWERED);
    render(<ChatPage />);
    await ask("Q?", { k: "25" });
    expect((await screen.findByRole("alert")).textContent).toBe("Top-k must be a whole number from 1 to 20.");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("sends with Ctrl+Enter", async () => {
    const { requests } = answer(ANSWERED);
    render(<ChatPage />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Question"), "Q?");
    await user.keyboard("{Control>}{Enter}{/Control}");
    await screen.findByText(/See also/);
    expect(requests).toHaveLength(1);
  });
});

describe("ChatPage: citations and sources", () => {
  it("turns valid [n] into buttons and leaves unknown numbers as text", async () => {
    answer(ANSWERED);
    render(<ChatPage />);
    await ask("Q?");
    await screen.findByText(/See also/);
    const buttons = screen.getAllByRole("button", { name: /Show source/ });
    expect(buttons.map((b) => b.textContent)).toEqual(["[1]", "[1]", "[2]"]);
    expect(screen.getByText(/See also \[7\]/)).toBeTruthy();
  });

  it("highlights and focuses the source a citation points to", async () => {
    answer(ANSWERED);
    render(<ChatPage />);
    const user = await ask("Q?");
    await screen.findByText(/See also/);
    await user.click(screen.getAllByRole("button", { name: "Show source 2" })[0]);
    const sources = within(screen.getByRole("region", { name: "Sources" })).getAllByRole("listitem");
    expect(sources[1].getAttribute("aria-current")).toBe("true");
    expect(document.activeElement).toBe(sources[1]);
    expect(sources[0].getAttribute("aria-current")).toBeNull();
  });

  it("lists every source with its details and marks which ones the answer cites", async () => {
    answer(ANSWERED);
    render(<ChatPage />);
    await ask("Q?");
    await screen.findByText(/See also/);
    const [first, second, third] = within(screen.getByRole("region", { name: "Sources" })).getAllByRole("listitem");
    expect(first.textContent).toContain("acme-dms/1.0/deprecated-features/deprecated-features.md");
    expect(first.textContent).toContain("acme-dms · version 1.0 · deprecated-features · distance 0.213");
    expect(first.textContent).toContain("Classic Desktop client: deprecated in 1.0.");
    expect(second.textContent).toContain("page 3");
    expect(first.textContent).toContain("cited");
    expect(third.textContent).toContain("retrieved, not cited");
  });

  it("shows 'I don't know' answers without sources", async () => {
    answer({ answer: NO_ANSWER, no_answer: true, sources: [], model: "gemma3:4b", elapsed_ms: 900 });
    render(<ChatPage />);
    await ask("Unrelated?", { version: "9.9" });
    expect(await screen.findByText("No answer found in the documents.")).toBeTruthy();
    expect(screen.getByText(/remove the product or version filter/)).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Sources" })).toBeNull();
  });
});

describe("ChatPage: waiting and cancelling", () => {
  it("shows the elapsed time and a Cancel button while waiting, and blocks a second question", async () => {
    neverAnswer();
    render(<ChatPage />);
    await ask("Slow question?");
    const status = screen.getByRole("status");
    expect(status.textContent).toContain("Waiting for the answer");
    expect(status.textContent).toMatch(/\d+ s/);
    await userEvent.type(screen.getByLabelText("Question"), "Another?");
    expect((screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("cancel stops waiting, aborts the request and allows asking again", async () => {
    const { signals, fetch } = neverAnswer();
    render(<ChatPage />);
    const user = await ask("Slow question?");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText(/Stopped waiting/)).toBeTruthy();
    expect(signals[0].aborted).toBe(true);
    expect(screen.queryByRole("alert")).toBeNull(); // cancelling is not an error

    await user.click(screen.getByRole("button", { name: "Ask again" }));
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(screen.getAllByRole("article")).toHaveLength(2); // newest first: waiting again, then the cancelled one
  });
});

describe("ChatPage: errors", () => {
  it.each([
    [503, { detail: "Can't reach Ollama at http://127.0.0.1:11434." }, "A required service is unavailable. Can't reach Ollama at http://127.0.0.1:11434."],
    [502, { detail: "The model service failed: out of memory" }, "The model couldn't answer. The model service failed: out of memory"],
    [500, { detail: "Internal server error." }, "Something went wrong on the server. The details are in the API server's log."],
    [
      422,
      { detail: [{ loc: ["body", "question"], msg: "String should have at most 2000 characters" }] },
      "Please check the request. question: String should have at most 2000 characters",
    ],
  ])("shows a readable message for HTTP %i", async (status, body, message) => {
    answer(body, status);
    render(<ChatPage />);
    await ask("Q?");
    expect((await screen.findByRole("alert")).textContent).toBe(message);
  });

  it("says the API is unreachable when the request fails, and can retry", async () => {
    const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () =>
      Promise.reject(new TypeError("Failed to fetch")),
    );
    vi.stubGlobal("fetch", fetch);
    render(<ChatPage />);
    const user = await ask("Q?");
    expect((await screen.findByRole("alert")).textContent).toContain("Can't reach the API");
    fetch.mockImplementationOnce(async () => Response.json(ANSWERED));
    await user.click(screen.getByRole("button", { name: "Ask again" }));
    expect(await screen.findByText(/See also/)).toBeTruthy();
  });
});
