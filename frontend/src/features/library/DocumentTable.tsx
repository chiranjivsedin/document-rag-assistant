import { useState } from "react";

import { deleteDocument, documentFileUrl, setDocumentTags } from "../../api/client";
import { BUILTIN_KEYS, type DocumentRecord, type TagCategory } from "../../api/types";
import { describeError, ErrorMessage, type ErrorText } from "../../components/ErrorMessage";
import { FileIcon } from "../../components/Icons";
import { ExtraTagField } from "../tags/ExtraTagField";
import { findCategory } from "../tags/useTags";
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

const FALLBACK_LABELS: Record<string, string> = { product: "Product", version: "Version", doc_type: "Type" };

interface Chip {
  key: string;
  category: string; // shown on hover
  text: string;
  mark: "retired" | "unlisted" | null;
}

/** A document's tags: its product, version and type (its folders; a file higher up the tree has fewer), then its
 * extra tags, each by its label from the master lists. Retired values and values missing from the lists are marked. */
function Tags({ document, categories }: { document: DocumentRecord; categories: TagCategory[] }) {
  const chips: Chip[] = [];
  for (const key of BUILTIN_KEYS) {
    const code = document[key];
    if (code === null) continue;
    const category = findCategory(categories, key);
    const value = category?.values.find((v) => v.code === code);
    const unlisted = document.unlisted.includes(key);
    chips.push({
      key,
      category: category?.label ?? FALLBACK_LABELS[key],
      text: value?.label ?? code,
      mark: unlisted ? "unlisted" : value && !value.active ? "retired" : null,
    });
  }
  for (const tag of document.tags ?? []) {
    chips.push({
      key: `tag-${tag.id}`,
      category: findCategory(categories, tag.category)?.label ?? tag.category,
      text: tag.label,
      mark: tag.active ? null : "retired",
    });
  }
  if (chips.length === 0) return <span className="doc-no-folders" title="Not in a product folder">—</span>;
  return (
    <span className="tag-list">
      {chips.map((chip) => (
        <span key={chip.key} className={chip.mark ? `tag tag-${chip.mark}` : "tag"} title={chip.category}>
          {chip.text}
          {chip.mark === "retired" && <span className="tag-mark"> · retired</span>}
          {chip.mark === "unlisted" && <span className="tag-mark"> · not in master list</span>}
        </span>
      ))}
    </span>
  );
}

/** Change a document's extra tags (its product, version and type are its folders: re-upload to change those). */
function EditTags({
  document,
  categories,
  onSaved,
  onClose,
}: {
  document: DocumentRecord;
  categories: TagCategory[];
  onSaved: (document: DocumentRecord) => void;
  onClose: () => void;
}) {
  const extraCategories = categories.filter((category) => !category.builtin);
  const current = (document.tags ?? []).map((tag) => tag.id);
  const [chosen, setChosen] = useState<Record<string, number[]>>(() =>
    Object.fromEntries(
      extraCategories.map((category) => [
        category.key,
        (document.tags ?? []).filter((tag) => tag.category === category.key).map((tag) => tag.id),
      ]),
    ),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ErrorText | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      onSaved(await setDocumentTags(document.id, extraCategories.flatMap((category) => chosen[category.key] ?? [])));
    } catch (failure) {
      setError(describeError(failure));
      setSaving(false);
    }
  }

  return (
    <div className="edit-tags" role="group" aria-label={`Tags of ${document.filename}`}>
      {extraCategories.map((category) => (
        <ExtraTagField
          key={category.key}
          category={category}
          idPrefix={`edit-${document.id}`}
          chosen={chosen[category.key] ?? []}
          keepIds={current}
          onChange={(ids) => setChosen((all) => ({ ...all, [category.key]: ids }))}
        />
      ))}
      {error && <ErrorMessage error={error} />}
      <div className="confirm-buttons">
        <button type="button" className="button button-primary button-small" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save tags"}
        </button>
        <button type="button" className="button button-ghost button-small" onClick={onClose} disabled={saving}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/** One row's actions, with an inline confirmation before a permanent delete. */
function Actions({
  document,
  onDeleted,
  canTag,
  onEditTags,
}: {
  document: DocumentRecord;
  onDeleted: (outcome: DeleteOutcome) => void;
  canTag: boolean;
  onEditTags: () => void;
}) {
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
      {canTag && (
        <button
          type="button"
          className="button button-ghost button-small"
          onClick={onEditTags}
          aria-label={`Edit tags of ${document.filename}`}
        >
          Edit tags
        </button>
      )}
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
  categories = [],
  onDeleted,
  onTagsSaved = () => {},
}: {
  documents: DocumentRecord[];
  categories?: TagCategory[]; // the master lists, for labels and the extra categories
  onDeleted: (outcome: DeleteOutcome) => void;
  onTagsSaved?: (document: DocumentRecord) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null); // the document whose tags are being edited
  const canTag = categories.some((category) => !category.builtin);
  return (
    <div className="doc-table-wrap">
    <table className="doc-table">
      <thead>
        <tr>
          <th scope="col">Document</th>
          <th scope="col">Tags</th>
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
            <td data-label="Tags">
              <Tags document={document} categories={categories} />
              {editing === document.id && (
                <EditTags
                  document={document}
                  categories={categories}
                  onClose={() => setEditing(null)}
                  onSaved={(saved) => {
                    setEditing(null);
                    onTagsSaved(saved);
                  }}
                />
              )}
            </td>
            <td data-label="Status">
              <StatusBadge document={document} />
            </td>
            <td className="doc-actions-cell">
              <Actions
                document={document}
                onDeleted={onDeleted}
                canTag={canTag}
                onEditTags={() => setEditing(editing === document.id ? null : document.id)}
              />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}
