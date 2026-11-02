import { useState } from "react";

import { deleteDocument, documentFileUrl } from "../../api/client";
import type { DocumentRecord } from "../../api/types";
import { describeError, type ErrorText } from "../../components/ErrorMessage";
import { FileIcon } from "../../components/Icons";
import { formatDate, formatSize, STATUS_LABELS } from "./format";

export interface DeleteOutcome {
  document: DocumentRecord;
  error: ErrorText | null; // null: deleted
}

function StatusBadge({ document }: { document: DocumentRecord }) {
  const label = STATUS_LABELS[document.status];
  return (
    <span className={`status-badge status-${document.status}`}>
      {document.status === "processing" && (
        <span className="dots" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
      )}
      {label}
      {document.status === "indexed" && document.chunk_count !== null && (
        <span className="status-extra">
          {" "}
          · {document.chunk_count} {document.chunk_count === 1 ? "passage" : "passages"}
        </span>
      )}
    </span>
  );
}

/** The folders a document sits in. A file put higher up the folder tree has fewer (or none). */
function Tags({ document }: { document: DocumentRecord }) {
  const parts = (
    [
      ["Product", document.product],
      ["Version", document.version],
      ["Type", document.doc_type],
    ] as Array<[string, string | null]>
  ).filter((part): part is [string, string] => part[1] !== null);
  if (parts.length === 0) return <span className="doc-no-folders" title="Not in a product folder">—</span>;
  return (
    <span className="tag-list">
      {parts.map(([name, value]) => (
        <span key={name} className="tag" title={name}>
          {value}
        </span>
      ))}
    </span>
  );
}

/** One row's actions, with an inline confirmation before a permanent delete. */
function Actions({ document, onDeleted }: { document: DocumentRecord; onDeleted: (outcome: DeleteOutcome) => void }) {
  const [step, setStep] = useState<"idle" | "confirm" | "deleting">("idle");

  async function confirmDelete() {
    setStep("deleting");
    try {
      await deleteDocument(document.id);
      onDeleted({ document, error: null });
    } catch (error) {
      setStep("idle");
      onDeleted({ document, error: describeError(error) });
    }
  }

  if (step !== "idle") {
    return (
      <div className="confirm-delete" role="group" aria-label={`Delete ${document.filename}`}>
        <span>Delete permanently? This removes the file, its passages in the index and its record.</span>
        <div className="confirm-buttons">
          <button type="button" className="button button-danger button-small" onClick={confirmDelete} disabled={step === "deleting"}>
            {step === "deleting" ? "Deleting…" : "Delete"}
          </button>
          <button
            type="button"
            className="button button-ghost button-small"
            onClick={() => setStep("idle")}
            disabled={step === "deleting"}
            autoFocus
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="doc-actions">
      <a
        className="button button-ghost button-small"
        href={documentFileUrl(document.id)}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={`Open ${document.filename}`}
      >
        Open
      </a>
      <a
        className="button button-ghost button-small"
        href={documentFileUrl(document.id, true)}
        download
        aria-label={`Download ${document.filename}`}
      >
        Download
      </a>
      <button
        type="button"
        className="button button-ghost button-small button-danger-text"
        onClick={() => setStep("confirm")}
        aria-label={`Delete ${document.filename}`}
      >
        Delete
      </button>
    </div>
  );
}

/** The library as a table; on narrow screens each row becomes a card (see .doc-table in styles.css). */
export function DocumentTable({
  documents,
  onDeleted,
}: {
  documents: DocumentRecord[];
  onDeleted: (outcome: DeleteOutcome) => void;
}) {
  return (
    <div className="doc-table-wrap">
    <table className="doc-table">
      <thead>
        <tr>
          <th scope="col">Document</th>
          <th scope="col">Product · version · type</th>
          <th scope="col">Status</th>
          <th scope="col">
            <span className="sr-only">Actions</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {documents.map((document) => (
          <tr key={document.id} className={`doc-row doc-${document.status}`}>
            <td data-label="Document" className="doc-name-cell">
              <span className="doc-name">
                <FileIcon />
                <strong>{document.filename}</strong>
              </span>
              <code className="doc-source" title={document.source}>
                {document.source}
              </code>
              <span className="doc-meta">
                <span className="doc-number">{formatSize(document.size_bytes)}</span> · added{" "}
                <time dateTime={document.added_at}>{formatDate(document.added_at)}</time>
                {document.origin === "folder" && " · found in the documents folder"}
              </span>
              {document.status === "failed" && document.error && <span className="doc-error">{document.error}</span>}
            </td>
            <td data-label="Product · version · type">
              <Tags document={document} />
            </td>
            <td data-label="Status">
              <StatusBadge document={document} />
            </td>
            <td className="doc-actions-cell">
              <Actions document={document} onDeleted={onDeleted} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}
