import { useRef, useState, type FormEvent } from "react";

import { uploadDocument } from "../../api/client";
import type { UploadedDocument } from "../../api/types";
import { describeError, ErrorMessage, type ErrorText } from "../../components/ErrorMessage";
import { ArrowUpIcon, CheckIcon, FileIcon } from "../../components/Icons";
import { formatSize } from "./format";
import { fileProblem, folderNameProblem, pyStrip, SUPPORTED_SUFFIXES } from "./validation";

const FIELDS = [
  { key: "product", label: "Product", placeholder: "e.g. documentum" },
  { key: "version", label: "Version", placeholder: "e.g. 16.2" },
  { key: "docType", label: "Document type", placeholder: "e.g. release-notes" },
] as const;

type FieldKey = (typeof FIELDS)[number]["key"];
type Problems = Partial<Record<FieldKey | "file", string>>;

export interface Suggestions {
  product: string[];
  version: string[];
  docType: string[];
}

type Outcome = { kind: "uploaded"; document: UploadedDocument } | { kind: "error"; error: ErrorText } | null;

/** Upload one PDF, Markdown or text file into the library under product / version / type. */
export function UploadCard({ suggestions, onUploaded }: { suggestions: Suggestions; onUploaded: (d: UploadedDocument) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [values, setValues] = useState<Record<FieldKey, string>>({ product: "", version: "", docType: "" });
  const [problems, setProblems] = useState<Problems>({});
  const [uploading, setUploading] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  function check(): Problems {
    const found: Problems = {};
    const fileIssue = fileProblem(file);
    if (fileIssue) found.file = fileIssue;
    for (const { key, label } of FIELDS) {
      const issue = folderNameProblem(values[key], label);
      if (issue) found[key] = issue;
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
        product: pyStrip(values.product),
        version: pyStrip(values.version),
        docType: pyStrip(values.docType),
      });
      onUploaded(document);
      setOutcome({ kind: "uploaded", document });
      setFile(null); // keep product, version and type for the next file
      if (fileInput.current) fileInput.current.value = "";
    } catch (error) {
      setOutcome({ kind: "error", error: describeError(error) });
    } finally {
      setUploading(false);
    }
  }

  const describedBy = (key: FieldKey | "file") => (problems[key] ? `upload-${key}-problem` : undefined);

  return (
    <section className="panel-card upload-card" aria-labelledby="upload-heading">
      <div className="upload-head">
        <h3 id="upload-heading">
          <ArrowUpIcon />
          Upload a document
        </h3>
        <p className="hint">
          PDF with selectable text, Markdown or plain text. It is stored under product / version / type and indexed
          straight away.
        </p>
      </div>

      <form className="upload-form" onSubmit={submit} noValidate>
        <div className="upload-file">
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
          {FIELDS.map(({ key, label, placeholder }) => (
            <div key={key} className="form-field">
              <label htmlFor={`upload-${key}`}>{label}</label>
              <input
                id={`upload-${key}`}
                value={values[key]}
                placeholder={placeholder}
                list={`upload-${key}-suggestions`}
                autoComplete="off"
                spellCheck={false}
                aria-invalid={problems[key] ? true : undefined}
                aria-describedby={describedBy(key)}
                onChange={(event) => {
                  const value = event.target.value;
                  setValues((current) => ({ ...current, [key]: value }));
                  setProblems(({ [key]: _ignored, ...rest }) => rest);
                }}
              />
              {problems[key] && (
                <span className="field-problem" id={`upload-${key}-problem`}>
                  {problems[key]}
                </span>
              )}
              <datalist id={`upload-${key}-suggestions`}>
                {suggestions[key].map((value) => (
                  <option key={value} value={value} />
                ))}
              </datalist>
            </div>
          ))}
        </div>

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
