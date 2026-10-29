import { useState } from "react";

import type { ChatRequest, ChatResponse } from "../../api/types";
import { ElapsedTimer } from "../../components/ElapsedTimer";
import { ErrorMessage, type ErrorText } from "../../components/ErrorMessage";
import { citedNumbers, splitCitations } from "./citations";
import { SourceList, sourceElementId } from "./SourceList";

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

function filtersLabel(request: ChatRequest): string {
  const parts = [
    request.product && `product ${request.product}`,
    request.version && `version ${request.version}`,
    `top ${request.k ?? 5}`,
  ];
  return parts.filter(Boolean).join(" · ");
}

/** One question and what came back for it: waiting, an answer with sources, an error, or cancelled. */
export function ChatTurn({ turn, onCancel, onRetry, canRetry }: Props) {
  const retry = (
    <button type="button" onClick={() => onRetry(turn.request)} disabled={!canRetry}>
      Ask again
    </button>
  );

  return (
    <article className="turn" aria-label={`Question: ${turn.request.question}`}>
      <div className="turn-question">
        <p>{turn.request.question}</p>
        <span className="turn-filters">{filtersLabel(turn.request)}</span>
      </div>

      {turn.status === "waiting" && (
        <div className="turn-waiting" role="status">
          <span>
            Waiting for the answer… <ElapsedTimer startedAt={turn.startedAt} />
          </span>
          <span className="hint">
            Answers can take a minute or more on a laptop CPU.
          </span>
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
        </div>
      )}

      {turn.status === "answered" && <Answer turnId={turn.id} response={turn.response} />}

      {turn.status === "failed" && (
        <div className="turn-actions">
          <ErrorMessage error={turn.error} />
          {retry}
        </div>
      )}

      {turn.status === "cancelled" && (
        <div className="turn-actions">
          <p className="hint">
            Stopped waiting. The server may still finish this answer in the background, so the next question can
            take a little longer.
          </p>
          {retry}
        </div>
      )}
    </article>
  );
}

function Answer({ turnId, response }: { turnId: number; response: ChatResponse }) {
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const seconds = (response.elapsed_ms / 1000).toFixed(1);

  if (response.no_answer) {
    return (
      <div className="answer answer-none">
        <p>
          <strong>No answer found in the documents.</strong> {response.answer}
        </p>
        <p className="hint">
          Try different wording, remove the product or version filter, or check that the documents for that version
          are indexed.
        </p>
        <p className="answer-meta">
          {response.model} · {seconds} s
        </p>
      </div>
    );
  }

  const showSource = (number: number) => {
    setHighlighted(number);
    const element = document.getElementById(sourceElementId(turnId, number));
    element?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    element?.focus({ preventScroll: true });
  };

  return (
    <div className="answer">
      <p className="answer-text">
        {splitCitations(response.answer, response.sources.length).map((part, index) =>
          "cite" in part ? (
            <button
              key={index}
              type="button"
              className="citation"
              onClick={() => showSource(part.cite)}
              aria-label={`Show source ${part.cite}`}
            >
              [{part.cite}]
            </button>
          ) : (
            <span key={index}>{part.text}</span>
          ),
        )}
      </p>
      <p className="answer-meta">
        {response.model} · {seconds} s
      </p>
      {response.sources.length > 0 && (
        <SourceList
          turnId={turnId}
          sources={response.sources}
          cited={citedNumbers(response.answer, response.sources.length)}
          highlighted={highlighted}
        />
      )}
    </div>
  );
}
