import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { postChat } from "../../api/client";
import type { ChatRequest } from "../../api/types";
import { describeError } from "../../components/ErrorMessage";
import { ChatTurn, type Turn } from "./ChatTurn";

const MAX_QUESTION = 2000; // same limits as ChatRequest in src/rag/api/schemas.py
const MAX_FILTER = 100;

/** Ask questions through POST /chat. History lives in memory for this browser session only. */
export function ChatPage() {
  const [question, setQuestion] = useState("");
  const [product, setProduct] = useState("");
  const [version, setVersion] = useState("");
  const [k, setK] = useState("5");
  const [formError, setFormError] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]); // newest first
  const controller = useRef<AbortController | null>(null);
  const nextId = useRef(1);

  useEffect(() => () => controller.current?.abort(), []);

  const waiting = turns.some((turn) => turn.status === "waiting");

  const update = (id: number, change: Partial<Turn>) =>
    setTurns((all) => all.map((turn) => (turn.id === id ? ({ ...turn, ...change } as Turn) : turn)));

  async function send(request: ChatRequest) {
    const id = nextId.current++;
    const abort = new AbortController();
    controller.current = abort;
    setTurns((all) => [{ id, request, startedAt: Date.now(), status: "waiting" }, ...all]);
    try {
      const response = await postChat(request, abort.signal);
      update(id, { status: "answered", response });
    } catch (error) {
      if (abort.signal.aborted) update(id, { status: "cancelled" });
      else update(id, { status: "failed", error: describeError(error) });
    } finally {
      if (controller.current === abort) controller.current = null;
    }
  }

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (waiting) return;
    const text = question.trim();
    const topK = Number(k);
    if (!text) {
      setFormError("Type a question first.");
      return;
    }
    if (!Number.isInteger(topK) || topK < 1 || topK > 20) {
      setFormError("Top-k must be a whole number from 1 to 20.");
      return;
    }
    setFormError(null);
    const request: ChatRequest = { question: text, k: topK };
    if (product.trim()) request.product = product.trim();
    if (version.trim()) request.version = version.trim();
    setQuestion("");
    void send(request);
  }

  function onQuestionKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) submit();
  }

  return (
    <div className="chat">
      {/* noValidate: our own checks below show one consistent message instead of the browser's validation bubble. */}
      <form className="chat-form" onSubmit={submit} aria-label="Ask a question" noValidate>
        <label className="field field-wide">
          Question
          <textarea
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={onQuestionKeyDown}
            maxLength={MAX_QUESTION}
            rows={3}
            placeholder="e.g. Which features are deprecated?"
          />
        </label>
        <div className="chat-options">
          <label className="field">
            Product (optional)
            <input value={product} onChange={(e) => setProduct(e.target.value)} maxLength={MAX_FILTER} placeholder="e.g. documentum" />
          </label>
          <label className="field">
            Version (optional)
            <input value={version} onChange={(e) => setVersion(e.target.value)} maxLength={MAX_FILTER} placeholder="e.g. 16.2" />
          </label>
          <label className="field field-narrow">
            Top-k
            <input type="number" value={k} onChange={(e) => setK(e.target.value)} min={1} max={20} step={1} />
          </label>
          <button type="submit" className="primary" disabled={waiting || !question.trim()}>
            Ask
          </button>
        </div>
        <p className="hint">
          Filters match the folder names in <code>documents/</code> exactly; leave them empty to search everything.
          Ctrl+Enter sends.
        </p>
        {formError && (
          <p className="form-error" role="alert">
            {formError}
          </p>
        )}
      </form>

      {turns.length === 0 ? (
        <p className="hint">Answers come only from the indexed documents, with numbered sources you can click.</p>
      ) : (
        <div className="turns">
          {turns.map((turn) => (
            <ChatTurn
              key={turn.id}
              turn={turn}
              onCancel={() => controller.current?.abort()}
              onRetry={(request) => void send(request)}
              canRetry={!waiting}
            />
          ))}
        </div>
      )}
    </div>
  );
}
