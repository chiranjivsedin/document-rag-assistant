import { useCallback, useEffect, useState } from "react";

import { listDocuments } from "../../api/client";
import type { DocumentRecord } from "../../api/types";
import { describeError, type ErrorText } from "../../components/ErrorMessage";
import { isWaiting } from "./format";

export type ListState =
  | { kind: "idle" } // not opened yet: the list loads the first time the tab is shown
  | { kind: "loading" }
  | { kind: "loaded"; documents: DocumentRecord[]; refreshing: boolean; refreshError: ErrorText | null }
  | { kind: "error"; error: ErrorText };

/**
 * The document list (GET /documents). Loads when the tab is opened and on reload(); while the tab is open and a
 * document is pending or processing, checks again every `pollIntervalMs` until all have finished. A failed refresh
 * keeps the last list and reports the problem; the next check tries again.
 */
export function useDocuments(active: boolean, pollIntervalMs: number) {
  const [state, setState] = useState<ListState>({ kind: "idle" });
  const [request, setRequest] = useState(0); // a new value loads the list again
  const reload = useCallback(() => setRequest((n) => n + 1), []);

  useEffect(() => {
    if (active) reload();
  }, [active, reload]);

  useEffect(() => {
    if (request === 0) return;
    const controller = new AbortController();
    setState((current) => (current.kind === "loaded" ? { ...current, refreshing: true } : { kind: "loading" }));
    listDocuments(controller.signal)
      .then((documents) => {
        if (!controller.signal.aborted) setState({ kind: "loaded", documents, refreshing: false, refreshError: null });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const problem = describeError(error);
        setState((current) =>
          current.kind === "loaded"
            ? { ...current, refreshing: false, refreshError: problem }
            : { kind: "error", error: problem },
        );
      });
    return () => controller.abort();
  }, [request]);

  const waiting = state.kind === "loaded" && !state.refreshing && state.documents.some(isWaiting);
  useEffect(() => {
    if (!active || !waiting) return;
    const timer = setTimeout(reload, pollIntervalMs);
    return () => clearTimeout(timer);
  }, [active, waiting, state, pollIntervalMs, reload]);

  /** Show a document straight away (e.g. one just uploaded), before the next load includes it. */
  const add = useCallback((document: DocumentRecord) => {
    setState((current) =>
      current.kind === "loaded"
        ? { ...current, documents: [document, ...current.documents.filter((d) => d.id !== document.id)] }
        : { kind: "loaded", documents: [document], refreshing: false, refreshError: null },
    );
  }, []);

  /** Stop showing a deleted document straight away. */
  const remove = useCallback((id: string) => {
    setState((current) =>
      current.kind === "loaded" ? { ...current, documents: current.documents.filter((d) => d.id !== id) } : current,
    );
  }, []);

  return { state, reload, add, remove };
}
