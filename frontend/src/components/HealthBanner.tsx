import { useEffect, useState } from "react";

import { getHealth } from "../api/client";
import type { HealthResponse } from "../api/types";

type HealthState =
  | { kind: "checking" }
  | { kind: "ok" | "unavailable"; health: HealthResponse }
  | { kind: "error"; message: string };

/** Shows whether the API, Ollama and the database are up (GET /health); checked on load and on demand. */
export function HealthBanner() {
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

  const recheck = (
    <button type="button" onClick={() => setAttempt((n) => n + 1)}>
      Check again
    </button>
  );

  switch (state.kind) {
    case "checking":
      // Can take ~12 s when the database is down (its connection timeout).
      return (
        <div className="banner banner-checking" role="status">
          Checking the API, Ollama and the database…
        </div>
      );
    case "ok":
      return (
        <div className="banner banner-ok" role="status">
          <span>
            Services OK · chat model <code>{state.health.llm_model}</code> · embedding model{" "}
            <code>{state.health.embedding_model}</code>
          </span>
          {recheck}
        </div>
      );
    case "unavailable":
      return (
        <div className="banner banner-problem" role="alert">
          <div>
            <strong>Services unavailable.</strong>
            <ul>
              {state.health.problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          </div>
          {recheck}
        </div>
      );
    case "error":
      return (
        <div className="banner banner-problem" role="alert">
          <span>{state.message}</span>
          {recheck}
        </div>
      );
  }
}
