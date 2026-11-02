import type { DocumentRecord, DocumentStatus } from "../../api/types";

/** 512 B, 2.4 KB, 13.1 MB: binary units, one decimal under 10. */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes;
  let unit = -1;
  do {
    value /= 1024;
    unit += 1;
  } while (value >= 1024 && unit < units.length - 1);
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/** A date and time in the reader's own locale and time zone. */
export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export const STATUS_LABELS: Record<DocumentStatus, string> = {
  pending: "Pending",
  processing: "Processing",
  indexed: "Indexed",
  failed: "Failed",
};

/** Still waiting for indexing to finish: the list keeps checking while any document is like this. */
export function isWaiting(document: DocumentRecord): boolean {
  return document.status === "pending" || document.status === "processing";
}

/** Does a document match the search text? Looks at its name, path, product, version and type. */
export function matchesSearch(document: DocumentRecord, search: string): boolean {
  const needle = search.trim().toLowerCase();
  if (!needle) return true;
  return [document.filename, document.source, document.product, document.version, document.doc_type].some(
    (value) => value?.toLowerCase().includes(needle),
  );
}

/** Distinct non-empty values, sorted, for the upload form's suggestions. */
export function distinct(values: Array<string | null>): string[] {
  return [...new Set(values.filter((value): value is string => Boolean(value)))].sort((a, b) =>
    a.localeCompare(b, undefined, { numeric: true }),
  );
}
