import { useState } from "react";

import type { ChatRequest, ChatResponse } from "../../api/types";
import { ElapsedTimer } from "../../components/ElapsedTimer";
import { ErrorMessage, type ErrorText } from "../../components/ErrorMessage";
import { CheckIcon, CopyIcon, InfoIcon, RetryIcon, SparkIcon } from "../../components/Icons";
import { citedNumbers, splitCitations } from "./citations";
import { previewElementId, SourceList, sourceElementId } from "./SourceList";

export type Turn = { id: number; request: ChatRequest; startedAt: number } & (
  | { status: "waiting" }
  | { status: "answered"; response: ChatResponse }
  | { status: "failed"; error: ErrorText }
  | { status: "cancelled" }
);

interface Props {
  turn: Turn;
  onCancel: () => void;
  onRetry: (request: ChatRequest) => void;
  canRetry: boolean;
}

/** The search scope a question was asked with, as chips. */
function scopeChips(request: ChatRequest): string[] {
  const chips = [request.product && `product ${request.product}`, request.version && `version ${request.version}`];
  const filters = chips.filter((chip): chip is string => Boolean(chip));
  return [...(filters.length ? filters : ["all documents"]), `top ${request.k ?? 5}`];
}

export function turnElementId(id: number): string {
  return `turn-${id}`;
}

/** One question and what came back for it: waiting, an answer with sources, an error, or cancelled. */
export function ChatTurn({ turn, onCancel, onRetry, canRetry }: Props) {
  const retry = (
    <button type="button" className="button button-ghost" onClick={() => onRetry(turn.request)} disabled={!canRetry}>
      <RetryIcon />
      Ask again
    </button>
  );

  return (
    <article className="turn" id={turnElementId(turn.id)} aria-label={`Question: ${turn.request.question}`}>
      <header className="turn-head">
        <h2 className="turn-question">{turn.request.question}</h2>
        <ul className="scope" aria-label="Search scope">
          {scopeChips(turn.request).map((chip) => (
            <li key={chip} className="tag">
              {chip}
            </li>
          ))}
        </ul>
      </header>

      {turn.status === "waiting" && (
        <div className="waiting" role="status">
          <div className="waiting-row">
            <span className="dots" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
            <span>
              Searching your documents and writing the answer… <ElapsedTimer startedAt={turn.startedAt} />
            </span>
            <button type="button" className="button button-ghost button-small" onClick={onCancel}>
              Cancel
            </button>
          </div>
          <div className="skeleton" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <p className="hint">Answers can take a minute or more on a laptop CPU.</p>
        </div>
      )}

      {turn.status === "answered" && <Answer turnId={turn.id} response={turn.response} />}

      {turn.status === "failed" && (
        <div className="turn-problem">
          <ErrorMessage error={turn.error} />
          {retry}
        </div>
      )}

      {turn.status === "cancelled" && (
        <div className="turn-problem">
          <p className="banner banner-muted">
            <InfoIcon />
            <span className="banner-body">
              Stopped waiting. The server may still finish this answer in the background, so the next question can
              take a little longer.
            </span>
          </p>
          {retry}
        </div>
      )}
    </article>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // clipboard not available (insecure context, permissions): nothing to do
    }
  };
  return (
    <button type="button" className="button button-ghost button-small" onClick={copy}>
      {copied ? <CheckIcon /> : <CopyIcon />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function Answer({ turnId, response }: { turnId: number; response: ChatResponse }) {
  const [selected, setSelected] = useState<number | null>(null);
  const seconds = (response.elapsed_ms / 1000).toFixed(1);
  const meta = `Answered by ${response.model} in ${seconds} s`;

  if (response.no_answer) {
    return (
      <div className="answer answer-none">
        <div className="answer-none-head">
          <InfoIcon />
          <p className="answer-title">No answer found in the documents.</p>
        </div>
        <p>{response.answer}</p>
        <p className="hint">
          Try different wording, remove the product or version filter, or check that the documents for that version
          are indexed.
        </p>
        <p className="answer-meta">{meta}</p>
      </div>
    );
  }

  const select = (number: number) => {
    setSelected(number);
    // Wait for the preview to render, then bring the card and the passage into view.
    setTimeout(() => {
      document.getElementById(sourceElementId(turnId, number))?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
      document.getElementById(previewElementId(turnId))?.focus({ preventScroll: true });
    }, 0);
  };

  return (
    <>
      {response.sources.length > 0 && (
        <SourceList
          turnId={turnId}
          sources={response.sources}
          cited={citedNumbers(response.answer, response.sources.length)}
          selected={selected}
          onSelect={select}
        />
      )}
      <section className="answer" aria-label="Answer">
        <div className="section-head">
          <h3>
            <SparkIcon />
            Answer
          </h3>
        </div>
        <p className="answer-text">
          {splitCitations(response.answer, response.sources.length).map((part, index) =>
            "cite" in part ? (
              <button
                key={index}
                type="button"
                className={part.cite === selected ? "cite cite-active" : "cite"}
                onClick={() => select(part.cite)}
                aria-label={`Show source ${part.cite}`}
              >
                <span className="cite-bracket">[</span>
                {part.cite}
                <span className="cite-bracket">]</span>
              </button>
            ) : (
              <span key={index}>{part.text}</span>
            ),
          )}
        </p>
        <footer className="answer-foot">
          <span className="answer-meta">{meta}</span>
          <CopyButton text={response.answer} />
        </footer>
      </section>
    </>
  );
}
