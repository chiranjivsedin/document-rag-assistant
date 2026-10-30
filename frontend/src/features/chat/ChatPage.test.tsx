import { render, screen, waitFor, within } from "@testing-library/react";
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

/** The chips under the question showing its filters and top-k. */
function scopeChips() {
  return within(screen.getByRole("list", { name: "Search scope" }))
    .getAllByRole("listitem")
    .map((chip) => chip.textContent);
}

/** The source cards in the strip (not the tag chips in the passage preview). */
function sourceCards() {
  return within(screen.getByRole("region", { name: "Sources" }))
    .getAllByRole("listitem")
    .filter((item) => !item.closest('[aria-label="Details"]'));
}

/** The detail tags of the passage preview currently shown. */
function previewTags() {
  return within(screen.getByRole("list", { name: "Details" }))
    .getAllByRole("listitem")
    .map((tag) => tag.textContent);
}

describe("ChatPage: asking", () => {
  it("sends the question with trimmed filters and k, and shows the answer with model and time", async () => {
    const { fetch, requests } = answer(ANSWERED);
    render(<ChatPage />);
    await ask("  What is deprecated?  ", { product: " acme-dms ", version: "1.0", k: "3" });

    expect(await screen.findByText(/See also/)).toBeTruthy();
    expect(fetch).toHaveBeenCalledWith("/chat", expect.objectContaining({ method: "POST" }));
    expect(requests).toEqual([{ question: "What is deprecated?", k: 3, product: "acme-dms", version: "1.0" }]);
    expect(screen.getByText("Answered by gemma3:4b in 18.4 s")).toBeTruthy();
    expect(scopeChips()).toEqual(["product acme-dms", "version 1.0", "top 3"]);
    expect((screen.getByLabelText("Question") as HTMLTextAreaElement).value).toBe(""); // ready for the next question
  });

  it("leaves out empty filters (they would mean 'no filter' anyway)", async () => {
    const { requests } = answer(ANSWERED);
    render(<ChatPage />);
    await ask("Q?", { product: "   " });
    await screen.findByText(/See also/);
    expect(requests).toEqual([{ question: "Q?", k: 5 }]);
    expect(scopeChips()).toEqual(["all documents", "top 5"]);
  });

  it("offers example questions that fill the composer without sending", async () => {
    const { fetch } = answer(ANSWERED);
    render(<ChatPage />);
    await userEvent.click(screen.getByRole("button", { name: /Which features are deprecated\?/ }));
    expect((screen.getByLabelText("Question") as HTMLTextAreaElement).value).toBe("Which features are deprecated?");
    expect(document.activeElement).toBe(screen.getByLabelText("Question"));
    expect(fetch).not.toHaveBeenCalled();
  });

  it("places a question and version handed over from another screen", async () => {
    answer(ANSWERED);
    const { rerender } = render(<ChatPage />);
    rerender(<ChatPage prefill={{ id: 1, question: "What is new in version 16.2?", version: "16.2" }} />);
    expect((screen.getByLabelText("Question") as HTMLTextAreaElement).value).toBe("What is new in version 16.2?");
    expect((screen.getByLabelText("Version (optional)") as HTMLInputElement).value).toBe("16.2");
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

  it("sends with Enter, and with Ctrl+Enter", async () => {
    const { requests } = answer(ANSWERED);
    render(<ChatPage />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Question"), "First?{Enter}");
    await screen.findByText(/See also/);
    await user.type(screen.getByLabelText("Question"), "Second?");
    await user.keyboard("{Control>}{Enter}{/Control}");
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(requests.map((r) => (r as { question: string }).question)).toEqual(["First?", "Second?"]);
  });

  it("starts a new line with Shift+Enter instead of sending", async () => {
    const { fetch } = answer(ANSWERED);
    render(<ChatPage />);
    await userEvent.type(screen.getByLabelText("Question"), "Line one{Shift>}{Enter}{/Shift}line two");
    expect((screen.getByLabelText("Question") as HTMLTextAreaElement).value).toBe("Line one\nline two");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("reports each asked question for the sidebar's history", async () => {
    answer(ANSWERED);
    const onHistoryChange = vi.fn();
    render(<ChatPage onHistoryChange={onHistoryChange} />);
    await ask("Q?");
    await screen.findByText(/See also/);
    expect(onHistoryChange).toHaveBeenLastCalledWith([{ id: 1, question: "Q?" }]);
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

  it("a citation selects its source and opens and focuses the passage preview", async () => {
    answer(ANSWERED);
    render(<ChatPage />);
    const user = await ask("Q?");
    await screen.findByText(/See also/);
    expect(screen.getByText("Select a source or a citation to read the passage.")).toBeTruthy();
    await user.click(screen.getAllByRole("button", { name: "Show source 2" })[0]);
    const cards = sourceCards();
    expect(cards[1].getAttribute("aria-current")).toBe("true");
    expect(cards[0].getAttribute("aria-current")).toBeNull();
    const preview = screen.getByLabelText("Source 2");
    expect(preview.textContent).toContain("Acme Web replaces the desktop client.");
    await waitFor(() => expect(document.activeElement).toBe(preview));
  });

  it("shows each source in the strip and its full details in the preview", async () => {
    answer(ANSWERED);
    render(<ChatPage />);
    const user = await ask("Q?");
    await screen.findByText(/See also/);
    const [first, second, third] = sourceCards();
    expect(first.textContent).toContain("deprecated-features.md");
    expect(first.textContent).toContain("Version 1.0");
    expect(first.textContent).toContain("cited");
    expect(second.textContent).toContain("Version 1.0, page 3");
    expect(third.textContent).toContain("retrieved, not cited");
    expect(screen.getByText("2 of 3 cited")).toBeTruthy();

    await user.click(within(first).getByRole("button"));
    expect(within(first).getByRole("button").getAttribute("aria-pressed")).toBe("true");
    const preview = screen.getByLabelText("Source 1");
    expect(preview.textContent).toContain("acme-dms/1.0/deprecated-features/deprecated-features.md");
    expect(previewTags()).toEqual(["acme-dms", "version 1.0", "deprecated-features", "distance 0.213", "cited"]);
    expect(preview.textContent).toContain("Classic Desktop client: deprecated in 1.0.");

    await user.click(within(third).getByRole("button"));
    expect(previewTags()).toContain("retrieved, not cited");
  });

  it("shows the whole passage in the preview, not a shortened start (regression)", async () => {
    // The API used to send only the first 300 characters, so previews stopped mid-sentence.
    const passage =
      "# Acme DMS 1.0 Deprecated Features\n\n" +
      "- The Classic Desktop client is deprecated in 1.0 and will be removed in 2.0. Use Acme Web instead.\n".repeat(4) +
      "- Basic authentication for the REST API is deprecated in 1.0. Use token authentication instead.";
    const sources = [{ ...ANSWERED.sources[0], excerpt: passage }];
    answer({ ...ANSWERED, answer: "Basic authentication is deprecated in 1.0 [1].", sources });
    render(<ChatPage />);
    const user = await ask("Q?");
    await user.click(await screen.findByRole("button", { name: "Show source 1" }));
    const excerpt = screen.getByLabelText("Source 1").querySelector("blockquote")!;
    expect(passage.length).toBeGreaterThan(300);
    expect(excerpt.textContent).toBe(passage);
  });

  it("every citation selects exactly its own source (regression)", async () => {
    answer(ANSWERED);
    render(<ChatPage />);
    const user = await ask("Q?");
    await screen.findByText(/See also/);
    for (const citation of screen.getAllByRole("button", { name: /^Show source \d+$/ })) {
      const number = Number(citation.textContent!.replace(/\D/g, ""));
      await user.click(citation);
      const selected = sourceCards().filter((card) => card.getAttribute("aria-current") === "true");
      expect(selected).toHaveLength(1);
      expect(within(selected[0]).getByRole("button").textContent).toContain(String(number));
      expect(screen.getByLabelText(`Source ${number}`).textContent).toContain(ANSWERED.sources[number - 1].excerpt);
    }
  });

  it("makes grouped citations like [1, 2] and [1-3] clickable and maps each to its source (regression)", async () => {
    answer({ ...ANSWERED, answer: "Deprecated [1, 2]. Removed [1-3]. Unknown [2, 7]." });
    render(<ChatPage />);
    const user = await ask("Q?");
    await screen.findByText(/Unknown/);
    const citations = screen.getAllByRole("button", { name: /^Show source \d+$/ });
    expect(citations.map((c) => c.getAttribute("aria-label"))).toEqual([
      "Show source 1", "Show source 2", "Show source 1", "Show source 2", "Show source 3", "Show source 2",
    ]);
    expect(screen.getByText(/Unknown/).closest("p")!.textContent).toContain("[7]"); // no source 7: stays text
    await user.click(citations[4]);
    expect(screen.getByLabelText("Source 3").textContent).toContain("Unrelated passage.");
  });

  it.each([
    ["Enter", "{Enter}"],
    ["Space", " "],
  ])("opens the source from the keyboard with %s and moves focus to the passage (regression)", async (_name, keys) => {
    answer(ANSWERED);
    render(<ChatPage />);
    const user = await ask("Q?");
    await screen.findByText(/See also/);
    screen.getAllByRole("button", { name: "Show source 2" })[0].focus();
    await user.keyboard(keys);
    expect(sourceCards()[1].getAttribute("aria-current")).toBe("true");
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Source 2")));
  });

  it("scrolls the Sources block into view on every activation, including the same citation again (regression)", async () => {
    answer(ANSWERED);
    const scrolled: Element[] = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) {
      scrolled.push(this);
    };
    try {
      render(<ChatPage />);
      const user = await ask("Q?");
      await screen.findByText(/See also/);
      const sources = screen.getByRole("region", { name: "Sources" });
      scrolled.length = 0; // ignore the scroll to the new question
      const citation = screen.getAllByRole("button", { name: "Show source 1" })[0];
      await user.click(citation);
      await waitFor(() => expect(scrolled).toContain(sources));
      scrolled.length = 0;
      await user.click(citation);
      await waitFor(() => expect(scrolled).toContain(sources));
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });

  it("points each citation at the passage it opens and names the file on hover", async () => {
    answer(ANSWERED);
    render(<ChatPage />);
    const user = await ask("Q?");
    await screen.findByText(/See also/);
    const citation = screen.getAllByRole("button", { name: "Show source 2" })[0];
    const target = citation.getAttribute("aria-controls")!;
    expect(document.getElementById(target)).not.toBeNull(); // the hint before anything is selected
    expect(citation.getAttribute("title")).toBe("notes.pdf");
    await user.click(citation);
    expect(document.getElementById(target)).toBe(screen.getByLabelText("Source 2"));
  });

  it("copies the answer text", async () => {
    answer(ANSWERED);
    render(<ChatPage />);
    const user = await ask("Q?"); // userEvent.setup() provides a stand-in clipboard
    await screen.findByText(/See also/);
    await user.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
    expect(await navigator.clipboard.readText()).toBe(ANSWERED.answer);
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
    expect(status.textContent).toContain("Searching your documents and writing the answer");
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
    expect(screen.getAllByRole("article")).toHaveLength(2); // the cancelled question, then the new attempt
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
