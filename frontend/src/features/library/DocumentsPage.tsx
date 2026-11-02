import { useMemo, useState } from "react";

import { startIngest } from "../../api/client";
import type { DocumentRecord, DocumentStatus } from "../../api/types";
import { describeError, ErrorMessage, type ErrorText } from "../../components/ErrorMessage";
import { AlertIcon, CheckIcon, IndexIcon, RetryIcon } from "../../components/Icons";
import { type DeleteOutcome, DocumentTable } from "./DocumentTable";
import { distinct, isWaiting, matchesSearch, STATUS_LABELS } from "./format";
import { type Suggestions, UploadCard } from "./UploadCard";
import { useDocuments } from "./useDocuments";

const COMMON_TYPES = ["release-notes", "deprecated-features"];

type Notice = { kind: "ok"; text: string } | { kind: "error"; error: ErrorText } | null;

function suggestionsFrom(documents: DocumentRecord[]): Suggestions {
  return {
    product: distinct(documents.map((d) => d.product)),
    version: distinct(documents.map((d) => d.version)),
    docType: distinct([...COMMON_TYPES, ...documents.map((d) => d.doc_type)]),
  };
}

/**
 * The document library: upload files, see whether each one is indexed, open or download the original, and delete
 * it. `active` is true while this tab is shown: the list loads (again) when the tab is opened, and keeps checking
 * only while it is shown and a document is still waiting to be indexed.
 */
export function DocumentsPage({ active, pollIntervalMs = 3000 }: { active: boolean; pollIntervalMs?: number }) {
  const { state, reload, add, remove } = useDocuments(active, pollIntervalMs);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<DocumentStatus | "all">("all");
  const [notice, setNotice] = useState<Notice>(null);
  const [starting, setStarting] = useState(false);

  const documents = useMemo(() => (state.kind === "loaded" ? state.documents : []), [state]);
  const shown = documents.filter((d) => (statusFilter === "all" || d.status === statusFilter) && matchesSearch(d, search));
  const counts = {
    total: documents.length,
    indexed: documents.filter((d) => d.status === "indexed").length,
    waiting: documents.filter(isWaiting).length,
    failed: documents.filter((d) => d.status === "failed").length,
  };
  const canIndex = documents.some((d) => d.status === "pending" || d.status === "failed");
  const suggestions = useMemo(() => suggestionsFrom(documents), [documents]);

  function deleted({ document, error }: DeleteOutcome) {
    if (error) {
      setNotice({ kind: "error", error: { title: `Couldn't delete ${document.filename}: ${error.title}`, detail: error.detail } });
      reload(); // a deletion that stopped part-way may have changed what is left
    } else {
      remove(document.id);
      setNotice({ kind: "ok", text: `Deleted ${document.filename}.` });
    }
  }

  async function indexWaiting() {
    setStarting(true);
    setNotice(null);
    try {
      const result = await startIngest();
      setNotice({
        kind: "ok",
        text: result.kind === "started" ? "Indexing started." : "Indexing is already running; it picks these up.",
      });
      reload();
    } catch (error) {
      setNotice({ kind: "error", error: describeError(error) });
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="page library-page">
      <section className="hero-card hero-compact">
        <div>
          <h2>Document library</h2>
          <p>
            The documents the assistant answers from. Upload a file to add it, or put files in the server's documents
            folder: they appear here after the next indexing run.
          </p>
        </div>
        {state.kind === "loaded" && (
          <dl className="library-counts" aria-label="Library summary">
            <div>
              <dt>Documents</dt>
              <dd>{counts.total}</dd>
            </div>
            <div>
              <dt>Indexed</dt>
              <dd>{counts.indexed}</dd>
            </div>
            <div>
              <dt>Waiting</dt>
              <dd>{counts.waiting}</dd>
            </div>
            <div>
              <dt>Failed</dt>
              <dd>{counts.failed}</dd>
            </div>
          </dl>
        )}
      </section>

      <UploadCard
        suggestions={suggestions}
        onUploaded={(document) => {
          setNotice(null);
          add(document);
          reload();
        }}
      />

      <section className="panel-card library-card" aria-labelledby="library-heading">
        <div className="library-toolbar">
          <h3 id="library-heading">Documents</h3>
          <div className="library-filters">
            <input
              type="search"
              className="search-field"
              placeholder="Search name, path, product, version or type"
              aria-label="Search documents"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            <select
              className="select-field"
              aria-label="Filter by status"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as DocumentStatus | "all")}
            >
              <option value="all">All statuses</option>
              {(Object.keys(STATUS_LABELS) as DocumentStatus[]).map((status) => (
                <option key={status} value={status}>
                  {STATUS_LABELS[status]}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="button button-ghost button-small"
              onClick={reload}
              disabled={state.kind === "loading" || (state.kind === "loaded" && state.refreshing)}
            >
              <RetryIcon />
              Refresh
            </button>
            {canIndex && (
              <button type="button" className="button button-primary button-small" onClick={indexWaiting} disabled={starting}>
                <IndexIcon />
                {starting ? "Starting…" : "Index pending documents"}
              </button>
            )}
          </div>
        </div>

        <div aria-live="polite">
          {notice?.kind === "ok" && (
            <p className="banner banner-ok" role="status">
              <CheckIcon />
              <span className="banner-body">{notice.text}</span>
            </p>
          )}
        </div>
        {notice?.kind === "error" && <ErrorMessage error={notice.error} />}
        {state.kind === "loaded" && state.refreshError && (
          <p className="banner banner-warn">
            <AlertIcon />
            <span className="banner-body">
              Couldn't refresh the list ({state.refreshError.title}: {state.refreshError.detail}). Showing the last one.
            </span>
          </p>
        )}

        {(state.kind === "idle" || state.kind === "loading") && (
          <p className="empty-note" role="status">
            Loading documents…
          </p>
        )}
        {state.kind === "error" && <ErrorMessage error={state.error} />}
        {state.kind === "loaded" && documents.length === 0 && (
          <p className="empty-note">
            No documents yet. Upload one above, or put files in the server's documents folder and run indexing.
          </p>
        )}
        {state.kind === "loaded" && documents.length > 0 && shown.length === 0 && (
          <p className="empty-note">No documents match this search.</p>
        )}
        {shown.length > 0 && (
          <>
            <p className="hint library-shown">
              Showing {shown.length} of {documents.length} {documents.length === 1 ? "document" : "documents"}
              {counts.waiting > 0 && active ? " · checking indexing progress every few seconds" : ""}
            </p>
            <DocumentTable documents={shown} onDeleted={deleted} />
          </>
        )}
      </section>
    </div>
  );
}
