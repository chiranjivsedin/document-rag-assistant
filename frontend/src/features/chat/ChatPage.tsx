import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { postChat } from "../../api/client";
import type { ChatRequest } from "../../api/types";
import { describeError } from "../../components/ErrorMessage";
import { AlertIcon, ArrowUpIcon, IndexIcon, LogoMark, SparkIcon, TagIcon } from "../../components/Icons";
import { ChatTurn, turnElementId, type Turn } from "./ChatTurn";

const MAX_QUESTION = 2000; // same limits as ChatRequest in src/rag/api/schemas.py
const MAX_FILTER = 100;

const SUGGESTIONS = [
  { icon: TagIcon, title: "Deprecated features", question: "Which features are deprecated?" },
  { icon: SparkIcon, title: "What's new", question: "What changed in the latest release?" },
  { icon: IndexIcon, title: "Requirements", question: "What are the system requirements?" },
];

/** A question to place in the composer from elsewhere in the app (e.g. the Release notes screen). */
export interface ChatPrefill {
  id: number; // changes on every request, so the same question can be placed twice
  question: string;
  version?: string;
}

export interface HistoryItem {
  id: number;
  question: string;
}

interface Props {
  prefill?: ChatPrefill | null;
  onHistoryChange?: (items: HistoryItem[]) => void;
}

/** Ask questions through POST /chat. History lives in memory for this browser session only. */
export function ChatPage({ prefill = null, onHistoryChange }: Props) {
  const [question, setQuestion] = useState("");
  const [product, setProduct] = useState("");
  const [version, setVersion] = useState("");
  const [k, setK] = useState("5");
  const [formError, setFormError] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]); // oldest first, like a conversation
  const controller = useRef<AbortController | null>(null);
  const nextId = useRef(1);
  const questionBox = useRef<HTMLTextAreaElement>(null);
  const latestId = turns.at(-1)?.id;

  useEffect(() => () => controller.current?.abort(), []);

  useEffect(() => {
    if (!prefill) return;
    setQuestion(prefill.question);
    setVersion(prefill.version ?? "");
    setFormError(null);
    questionBox.current?.focus();
  }, [prefill]);

  // Report the asked questions (for the sidebar's Recent list); only new questions change it.
  const historyKey = turns.map((turn) => turn.id).join(",");
  useEffect(() => {
    onHistoryChange?.(turns.map((turn) => ({ id: turn.id, question: turn.request.question })));
  }, [historyKey]);

  useEffect(() => {
    if (latestId) document.getElementById(turnElementId(latestId))?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [latestId]);

  // Grow the question box with its content (up to the max-height set in CSS).
  useLayoutEffect(() => {
    const box = questionBox.current;
    if (!box) return;
    box.style.height = "auto";
    box.style.height = `${box.scrollHeight}px`;
  }, [question]);

  const waiting = turns.some((turn) => turn.status === "waiting");

  const update = (id: number, change: Partial<Turn>) =>
    setTurns((all) => all.map((turn) => (turn.id === id ? ({ ...turn, ...change } as Turn) : turn)));

  async function send(request: ChatRequest) {
    const id = nextId.current++;
    const abort = new AbortController();
    controller.current = abort;
    setTurns((all) => [...all, { id, request, startedAt: Date.now(), status: "waiting" }]);
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
    setTimeout(() => questionBox.current?.focus(), 0); // the composer moves when the first question is sent
  }

  function onQuestionKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter starts a new line; never while an input method is composing text.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  function pickSuggestion(text: string) {
    setQuestion(text);
    questionBox.current?.focus();
  }

  const composer = (
    <div className="composer-wrap">
      {/* noValidate: our own checks show one consistent message instead of the browser's validation bubble. */}
      <form className="composer" onSubmit={submit} aria-label="Ask a question" noValidate>
        <label className="sr-only" htmlFor="chat-question">
          Question
        </label>
        <textarea
          id="chat-question"
          ref={questionBox}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={onQuestionKeyDown}
          maxLength={MAX_QUESTION}
          rows={1}
          placeholder={turns.length ? "Ask a follow-up question…" : "Ask about a version, its release notes or deprecated features…"}
        />
        <div className="composer-row">
          <div className="filters">
            <label className="pill-field">
              <span>Product</span>
              <input
                aria-label="Product (optional)"
                value={product}
                onChange={(e) => setProduct(e.target.value)}
                maxLength={MAX_FILTER}
                placeholder="Any"
                size={8}
              />
            </label>
            <label className="pill-field">
              <span>Version</span>
              <input
                aria-label="Version (optional)"
                value={version}
                onChange={(e) => setVersion(e.target.value)}
                maxLength={MAX_FILTER}
                placeholder="Any"
                size={6}
              />
            </label>
            <label className="pill-field">
              <span>Passages</span>
              <input
                aria-label="Top-k"
                type="number"
                value={k}
                onChange={(e) => setK(e.target.value)}
                min={1}
                max={20}
                step={1}
              />
            </label>
          </div>
          <button type="submit" className="send" aria-label="Ask" disabled={waiting || !question.trim()}>
            <ArrowUpIcon />
          </button>
        </div>
      </form>
      {formError && (
        <p className="banner banner-error" role="alert">
          <AlertIcon />
          <span className="banner-body">{formError}</span>
        </p>
      )}
      <p className="composer-note">
        Answers come only from your indexed documents. <kbd>Enter</kbd> to send, <kbd>Shift</kbd>+<kbd>Enter</kbd> for a
        new line.
      </p>
    </div>
  );

  if (turns.length === 0) {
    return (
      <div className="chat-home">
        <div className="home-hero">
          <LogoMark size={48} />
          <h2>What would you like to know?</h2>
          <p>
            Ask about versions, release notes and deprecated features. Every answer cites the passages it came from.
          </p>
        </div>
        {composer}
        <ul className="suggestions" aria-label="Example questions">
          {SUGGESTIONS.map(({ icon: Icon, title, question: text }) => (
            <li key={text}>
              <button type="button" className="suggestion" onClick={() => pickSuggestion(text)}>
                <span className="suggestion-icon">
                  <Icon />
                </span>
                <span className="suggestion-title">{title}</span>
                <span className="suggestion-text">{text}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div className="chat-thread">
      <div className="thread">
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
      <div className="dock">{composer}</div>
    </div>
  );
}
