import { useState, type FormEvent } from "react";

import { addTagCategory, addTagValue, ApiError, deleteTagValue, updateTagValue } from "../../api/client";
import type { TagCategory, TagValue } from "../../api/types";
import { describeError, ErrorMessage, type ErrorText } from "../../components/ErrorMessage";
import { AlertIcon, CheckIcon, PlusIcon, RetryIcon } from "../../components/Icons";
import { categoryKeyProblem, cleanLabel, folderNameProblem, labelProblem, pyStrip } from "../library/validation";
import { useTags } from "./useTags";

// The admin key lives in this tab's session storage only: never in the URL, never in local storage, gone when the
// tab closes. Storage can be unavailable (private windows, blocked site data); the key is then kept in memory.
const KEY_STORAGE = "rag.adminKey";
export const ADMIN_DISABLED = "Admin is disabled on this server";

function storedKey(): string | null {
  try {
    return sessionStorage.getItem(KEY_STORAGE);
  } catch {
    return null;
  }
}

function storeKey(key: string | null) {
  try {
    if (key === null) sessionStorage.removeItem(KEY_STORAGE);
    else sessionStorage.setItem(KEY_STORAGE, key);
  } catch {
    // kept in memory only
  }
}

type Notice = { kind: "ok"; text: string } | { kind: "error"; error: ErrorText } | null;

/** Runs an admin call with the key; returns whether it succeeded. */
type Run = (action: (adminKey: string) => Promise<unknown>, done: string) => Promise<boolean>;

function KeyForm({ onKey }: { onKey: (key: string) => void }) {
  const [key, setKey] = useState("");
  return (
    <form
      className="admin-key"
      onSubmit={(event) => {
        event.preventDefault();
        if (key) onKey(key);
      }}
    >
      <label className="form-field">
        <span>Admin key</span>
        <input type="password" autoComplete="off" value={key} onChange={(event) => setKey(event.target.value)} />
      </label>
      <button type="submit" className="button button-primary" disabled={!key}>
        Unlock editing
      </button>
      <p className="hint">
        Needed to change the lists (kept in this browser tab only). Reading the lists and tagging documents need no key.
      </p>
    </form>
  );
}

function ValueRow({ category, value, run }: { category: TagCategory; value: TagValue; run: Run | null }) {
  const [renaming, setRenaming] = useState(false);
  const [label, setLabel] = useState(value.label);
  const [problem, setProblem] = useState<string | null>(null);

  async function rename(event: FormEvent) {
    event.preventDefault();
    const issue = labelProblem(label);
    setProblem(issue);
    if (issue || !run) return;
    if (await run((key) => updateTagValue(key, value.id, { label: cleanLabel(label) }), `Renamed to "${cleanLabel(label)}".`)) {
      setRenaming(false);
    }
  }

  return (
    <tr className={value.active ? undefined : "tag-row-retired"}>
      <td>
        {renaming ? (
          <form className="inline-form" onSubmit={rename}>
            <input
              aria-label={`New label for ${value.code}`}
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              aria-invalid={problem ? true : undefined}
              autoFocus
            />
            <button type="submit" className="button button-primary button-small">
              Save
            </button>
            <button type="button" className="button button-ghost button-small" onClick={() => setRenaming(false)}>
              Cancel
            </button>
            {problem && <span className="field-problem">{problem}</span>}
          </form>
        ) : (
          <strong>{value.label}</strong>
        )}
      </td>
      <td>
        <code>{value.code}</code>
      </td>
      <td>{value.active ? <span className="tag tag-ok">Active</span> : <span className="tag tag-retired">Retired</span>}</td>
      <td className="doc-actions-cell">
        {run && !renaming && (
          <div className="doc-actions">
            <button
              type="button"
              className="button button-ghost button-small"
              aria-label={`Rename ${value.label}`}
              onClick={() => {
                setLabel(value.label);
                setProblem(null);
                setRenaming(true);
              }}
            >
              Rename
            </button>
            <button
              type="button"
              className="button button-ghost button-small"
              aria-label={`${value.active ? "Retire" : "Restore"} ${value.label}`}
              onClick={() =>
                run(
                  (key) => updateTagValue(key, value.id, { active: !value.active }),
                  value.active ? `Retired "${value.label}".` : `Restored "${value.label}".`,
                )
              }
            >
              {value.active ? "Retire" : "Restore"}
            </button>
            <button
              type="button"
              className="button button-ghost button-small button-danger-text"
              aria-label={`Delete ${value.label}`}
              onClick={() => run((key) => deleteTagValue(key, value.id), `Deleted "${value.label}" from ${category.label}.`)}
            >
              Delete
            </button>
          </div>
        )}
      </td>
    </tr>
  );
}

function AddValueForm({ category, run }: { category: TagCategory; run: Run }) {
  const [code, setCode] = useState("");
  const [label, setLabel] = useState("");
  const [problems, setProblems] = useState<{ code?: string; label?: string }>({});

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found = {
      code: folderNameProblem(code, "The code") ?? undefined,
      label: labelProblem(label) ?? undefined,
    };
    setProblems(found);
    if (found.code || found.label) return;
    const value = { code: pyStrip(code), label: cleanLabel(label) };
    if (await run((key) => addTagValue(key, category.key, value), `Added "${value.label}" to ${category.label}.`)) {
      setCode("");
      setLabel("");
    }
  }

  return (
    <form className="inline-form add-value" onSubmit={submit} noValidate aria-label={`Add a value to ${category.label}`}>
      <div className="form-field">
        <label htmlFor={`add-${category.key}-code`}>Code</label>
        <input
          id={`add-${category.key}-code`}
          value={code}
          onChange={(event) => setCode(event.target.value)}
          placeholder={category.key === "version" ? "e.g. 26.4" : "e.g. release-notes"}
          spellCheck={false}
          aria-invalid={problems.code ? true : undefined}
        />
        {problems.code && <span className="field-problem">{problems.code}</span>}
      </div>
      <div className="form-field">
        <label htmlFor={`add-${category.key}-label`}>Label</label>
        <input
          id={`add-${category.key}-label`}
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          aria-invalid={problems.label ? true : undefined}
        />
        {problems.label && <span className="field-problem">{problems.label}</span>}
      </div>
      <button type="submit" className="button button-ghost button-small">
        <PlusIcon />
        Add value
      </button>
    </form>
  );
}

function AddCategoryForm({ run }: { run: Run }) {
  const [key, setKey] = useState("");
  const [label, setLabel] = useState("");
  const [multiple, setMultiple] = useState(false);
  const [problems, setProblems] = useState<{ key?: string; label?: string }>({});

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found = { key: categoryKeyProblem(key) ?? undefined, label: labelProblem(label) ?? undefined };
    setProblems(found);
    if (found.key || found.label) return;
    const category = { key: pyStrip(key), label: cleanLabel(label), multiple };
    if (await run((adminKey) => addTagCategory(adminKey, category), `Added the category "${category.label}".`)) {
      setKey("");
      setLabel("");
      setMultiple(false);
    }
  }

  return (
    <section className="panel-card" aria-labelledby="add-category-heading">
      <h3 id="add-category-heading">Add a category</h3>
      <p className="hint">
        For example Module or Audience. Its key is permanent; its values are added below once it exists.
      </p>
      <form className="inline-form" onSubmit={submit} noValidate>
        <div className="form-field">
          <label htmlFor="new-category-key">Key</label>
          <input
            id="new-category-key"
            value={key}
            onChange={(event) => setKey(event.target.value)}
            placeholder="e.g. module"
            spellCheck={false}
            aria-invalid={problems.key ? true : undefined}
          />
          {problems.key && <span className="field-problem">{problems.key}</span>}
        </div>
        <div className="form-field">
          <label htmlFor="new-category-label">Category label</label>
          <input
            id="new-category-label"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            aria-invalid={problems.label ? true : undefined}
          />
          {problems.label && <span className="field-problem">{problems.label}</span>}
        </div>
        <label className="tag-choice">
          <input type="checkbox" checked={multiple} onChange={(event) => setMultiple(event.target.checked)} />
          Several values per document
        </label>
        <button type="submit" className="button button-primary button-small">
          <PlusIcon />
          Add category
        </button>
      </form>
    </section>
  );
}

/**
 * The master lists behind every tag: Product, Version and Document type (the documents' folders) and the extra
 * categories. Anyone can read them; changing them needs the admin key (the ADMIN_KEY setting on the server).
 */
export function TagsPage() {
  const tags = useTags();
  const [adminKey, setAdminKey] = useState<string | null>(storedKey);
  const [disabled, setDisabled] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  function rememberKey(key: string | null) {
    storeKey(key);
    setAdminKey(key);
  }

  const run: Run | null =
    adminKey && !disabled
      ? async (action, done) => {
          setNotice(null);
          try {
            await action(adminKey);
            setNotice({ kind: "ok", text: done });
            tags.refresh();
            return true;
          } catch (error) {
            if (error instanceof ApiError && error.status === 401) {
              rememberKey(null);
              setNotice({ kind: "error", error: { title: "Wrong admin key", detail: "Enter the admin key again." } });
            } else if (error instanceof ApiError && error.status === 403) {
              setDisabled(true);
              setNotice(null);
            } else {
              setNotice({ kind: "error", error: describeError(error) });
            }
            return false;
          }
        }
      : null;

  return (
    <div className="page tags-page">
      <section className="hero-card hero-compact">
        <div>
          <h2>Tag lists</h2>
          <p>
            Every tag a document carries comes from these lists, so the same thing is always spelled the same way.
            Retire a value to stop its new use (documents keep it); only a value nothing uses can be deleted.
          </p>
        </div>
        {adminKey && !disabled && (
          <button type="button" className="button button-ghost button-small" onClick={() => rememberKey(null)}>
            Lock editing
          </button>
        )}
      </section>

      {disabled && (
        <p className="banner banner-warn" role="status">
          <AlertIcon />
          <span className="banner-body">
            {ADMIN_DISABLED}: the lists can be read here, but changing them needs ADMIN_KEY set on the server.
          </span>
        </p>
      )}
      {!adminKey && !disabled && <KeyForm onKey={(key) => rememberKey(key)} />}

      <div aria-live="polite">
        {notice?.kind === "ok" && (
          <p className="banner banner-ok" role="status">
            <CheckIcon />
            <span className="banner-body">{notice.text}</span>
          </p>
        )}
      </div>
      {notice?.kind === "error" && <ErrorMessage error={notice.error} />}
      {tags.error && (
        <p className="banner banner-warn">
          <AlertIcon />
          <span className="banner-body">
            Couldn't load the lists ({tags.error}).{" "}
            <button type="button" className="button button-ghost button-small" onClick={tags.refresh}>
              <RetryIcon />
              Try again
            </button>
          </span>
        </p>
      )}
      {tags.loading && tags.categories.length === 0 && (
        <p className="empty-note" role="status">
          Loading the lists…
        </p>
      )}

      {tags.categories.map((category) => (
        <section key={category.key} className="panel-card tag-category" aria-labelledby={`category-${category.key}`}>
          <div className="library-toolbar">
            <h3 id={`category-${category.key}`}>{category.label}</h3>
            <span className="hint">
              <code>{category.key}</code>
              {category.builtin ? " · built in: the document's folder" : ""}
              {category.multiple ? " · several values per document" : " · one value per document"}
            </span>
          </div>
          {category.values.length === 0 ? (
            <p className="empty-note">No values yet.</p>
          ) : (
            <div className="doc-table-wrap">
              <table className="doc-table tag-table">
                <thead>
                  <tr>
                    <th scope="col">Label</th>
                    <th scope="col">Code</th>
                    <th scope="col">Status</th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {category.values.map((value) => (
                    <ValueRow key={`${value.id}-${value.label}`} category={category} value={value} run={run} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {run && <AddValueForm category={category} run={run} />}
        </section>
      ))}

      {run && tags.categories.length > 0 && <AddCategoryForm run={run} />}
    </div>
  );
}
