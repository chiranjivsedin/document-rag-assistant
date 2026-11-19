import { useRef, useState, type FormEvent } from "react";

import { uploadDocument } from "../../api/client";
import type { BuiltinKey, UploadedDocument } from "../../api/types";
import { describeError, ErrorMessage, type ErrorText } from "../../components/ErrorMessage";
import { AlertIcon, ArrowUpIcon, CheckIcon, FileIcon, RetryIcon } from "../../components/Icons";
import { ExtraTagField } from "../tags/ExtraTagField";
import { activeValues, findCategory, type TagsState } from "../tags/useTags";
import { formatSize } from "./format";
import { fileProblem, SUPPORTED_SUFFIXES } from "./validation";

// The three built-in categories, picked from their master lists (never typed): they become the file's folders.
const FIELDS: Array<{ key: BuiltinKey; label: string }> = [
  { key: "product", label: "Product" },
  { key: "version", label: "Version" },
  { key: "doc_type", label: "Document type" },
];

type Problems = Partial<Record<BuiltinKey | "file", string>>;

type Outcome = { kind: "uploaded"; document: UploadedDocument } | { kind: "error"; error: ErrorText } | null;

/** Upload one PDF, Markdown or text file into the library, described with values from the master lists only. */
export function UploadCard({ tags, onUploaded }: { tags: TagsState; onUploaded: (d: UploadedDocument) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [values, setValues] = useState<Record<BuiltinKey, string>>({ product: "", version: "", doc_type: "" });
  const [extra, setExtra] = useState<Record<string, number[]>>({});
  const [problems, setProblems] = useState<Problems>({});
  const [uploading, setUploading] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const extraCategories = tags.categories.filter((category) => !category.builtin);
  const offered = (key: BuiltinKey) => {
    const category = findCategory(tags.categories, key);
    return category ? activeValues(category) : [];
  };

  function check(): Problems {
    const found: Problems = {};
    const fileIssue = fileProblem(file);
    if (fileIssue) found.file = fileIssue;
    for (const { key, label } of FIELDS) {
      if (!offered(key).some((value) => value.code === values[key])) found[key] = `${label} is required.`;
    }
    return found;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found = check();
    setProblems(found);
    setOutcome(null);
    if (Object.keys(found).length > 0 || !file) return;

    setUploading(true);
    try {
      const document = await uploadDocument(file, {
        product: values.product,
        version: values.version,
        docType: values.doc_type,
        tagIds: extraCategories.flatMap((category) => extra[category.key] ?? []),
      });
      onUploaded(document);
      setOutcome({ kind: "uploaded", document });
      setFile(null); // keep the values and tags for the next file
      if (fileInput.current) fileInput.current.value = "";
    } catch (error) {
      setOutcome({ kind: "error", error: describeError(error) });
    } finally {
      setUploading(false);
    }
  }

  const describedBy = (key: BuiltinKey | "file") => (problems[key] ? `upload-${key}-problem` : undefined);

  return (
    <section className="panel-card upload-card" aria-labelledby="upload-heading">
      <div className="upload-head">
        <h3 id="upload-heading">
          <ArrowUpIcon />
          Upload a document
        </h3>
        <p className="hint">
          PDF with selectable text, Markdown or plain text. Choose its product, version and type from the master lists;
          it is stored under them and indexed straight away.
        </p>
      </div>

      {tags.error && (
        <p className="banner banner-warn">
          <AlertIcon />
          <span className="banner-body">
            Couldn't load the master lists ({tags.error}).{" "}
            <button type="button" className="button button-ghost button-small" onClick={tags.refresh}>
              <RetryIcon />
              Try again
            </button>
          </span>
        </p>
      )}

      <form className="upload-form" onSubmit={submit} noValidate>
        <div className="upload-file">
          {/* A visible caption like the other fields' labels, so every control lines up; the input keeps its own
              accessible name. */}
          <span className="field-label" aria-hidden="true">
            File
          </span>
          <label className="file-picker">
            <input
              ref={fileInput}
              type="file"
              accept={SUPPORTED_SUFFIXES.join(",")}
              aria-label="File"
              aria-invalid={problems.file ? true : undefined}
              aria-describedby={describedBy("file")}
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                setProblems(({ file: _ignored, ...rest }) => rest);
              }}
            />
            <FileIcon />
            <span className="file-picker-name">{file ? file.name : "Choose a file…"}</span>
            {file && <span className="file-picker-size">{formatSize(file.size)}</span>}
          </label>
          {problems.file && (
            <p className="field-problem" id="upload-file-problem">
              {problems.file}
            </p>
          )}
        </div>

        <div className="upload-fields">
          {FIELDS.map(({ key, label }) => (
            <div key={key} className="form-field">
              <label htmlFor={`upload-${key}`}>{label}</label>
              <select
                id={`upload-${key}`}
                className="select-field"
                value={values[key]}
                aria-invalid={problems[key] ? true : undefined}
                aria-describedby={describedBy(key)}
                onChange={(event) => {
                  const value = event.target.value;
                  setValues((current) => ({ ...current, [key]: value }));
                  setProblems(({ [key]: _ignored, ...rest }) => rest);
                }}
              >
                <option value="">{tags.loading ? "Loading…" : "Choose…"}</option>
                {offered(key).map((value) => (
                  <option key={value.id} value={value.code}>
                    {value.label}
                  </option>
                ))}
              </select>
              {problems[key] && (
                <span className="field-problem" id={`upload-${key}-problem`}>
                  {problems[key]}
                </span>
              )}
            </div>
          ))}
        </div>

        {extraCategories.length > 0 && (
          <div className="upload-tags" aria-label="Tags">
            {extraCategories.map((category) => (
              <ExtraTagField
                key={category.key}
                category={category}
                idPrefix="upload-tag"
                chosen={extra[category.key] ?? []}
                onChange={(ids) => setExtra((current) => ({ ...current, [category.key]: ids }))}
              />
            ))}
          </div>
        )}

        <div className="upload-actions">
          <button type="submit" className="button button-primary" disabled={uploading}>
            <ArrowUpIcon />
            {uploading ? "Uploading…" : "Upload"}
          </button>
        </div>
      </form>

      <div aria-live="polite">
        {outcome?.kind === "uploaded" && (
          <p className="banner banner-ok" role="status">
            <CheckIcon />
            <span className="banner-body">
              Uploaded <strong>{outcome.document.filename}</strong>.{" "}
              {outcome.document.ingest_job_id
                ? "Indexing has started; its status updates below."
                : "It will be indexed when indexing next runs (it waits while another run finishes, or until Ollama and the database are available)."}
            </span>
          </p>
        )}
      </div>
      {outcome?.kind === "error" && <ErrorMessage error={outcome.error} />}
    </section>
  );
}
