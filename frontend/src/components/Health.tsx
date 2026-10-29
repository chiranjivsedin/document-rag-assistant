import { useCallback, useEffect, useState } from "react";

import { getHealth } from "../api/client";
import type { HealthResponse } from "../api/types";
import { AlertIcon, RetryIcon } from "./Icons";

export type HealthState =
  | { kind: "checking" }
  | { kind: "ok" | "unavailable"; health: HealthResponse }
  | { kind: "error"; message: string };

/** GET /health on load and on demand. Shared by the status indicator and the problem notice. */
export function useHealth(): { state: HealthState; recheck: () => void } {
  const [state, setState] = useState<HealthState>({ kind: "checking" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "checking" });
    getHealth(controller.signal)
      .then((health) => {
        if (!controller.signal.aborted) setState({ kind: health.status, health });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setState({ kind: "error", message: error instanceof Error ? error.message : String(error) });
        }
      });
    return () => controller.abort();
  }, [attempt]);

  const recheck = useCallback(() => setAttempt((n) => n + 1), []);
  return { state, recheck };
}

const LABELS: Record<HealthState["kind"], string> = {
  checking: "Checking services…", // can take ~12 s when the database is down (its connection timeout)
  ok: "All services running",
  unavailable: "Services unavailable",
  error: "API unreachable",
};

/** Compact status for the sidebar: a coloured dot, one line of text and a re-check button. */
export function HealthStatus({ state, onRecheck }: { state: HealthState; onRecheck: () => void }) {
  const detail =
    state.kind === "ok"
      ? `Chat model ${state.health.llm_model}, embedding model ${state.health.embedding_model}`
      : state.kind === "unavailable"
        ? `${state.health.problems.length} problem${state.health.problems.length === 1 ? "" : "s"}`
        : state.kind === "error"
          ? "Is uvicorn running?"
          : "Ollama and the database";

  return (
    <div className={`status status-${state.kind}`} role="status">
      <span className="status-dot" aria-hidden="true" />
      <div className="status-text">
        <span className="status-label">{LABELS[state.kind]}</span>
        <span className="status-detail">{detail}</span>
      </div>
      <button
        type="button"
        className="icon-button"
        onClick={onRecheck}
        disabled={state.kind === "checking"}
        aria-label="Check again"
        title="Check again"
      >
        <RetryIcon />
      </button>
    </div>
  );
}

/** Full notice shown above the page when something is down, listing each problem. */
export function HealthAlert({ state, onRecheck }: { state: HealthState; onRecheck: () => void }) {
  let body;
  if (state.kind === "unavailable") {
    body = (
      <>
        <strong>Services unavailable.</strong>
        <ul>
          {state.health.problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      </>
    );
  } else if (state.kind === "error") {
    body = (
      <>
        <strong>Can't reach the API.</strong>
        <p>{state.message}</p>
      </>
    );
  } else {
    return null;
  }
  return (
    <div className="banner banner-warn" role="alert">
      <AlertIcon />
      <div className="banner-body">{body}</div>
      <button type="button" className="button button-ghost" onClick={onRecheck}>
        Check again
      </button>
    </div>
  );
}
