// Upload checks done in the browser, so most mistakes never reach the server. They mirror the server's own rules
// exactly (src/rag/dms/storage.py: check_folder_name() and clean_filename()); the server checks again either way.
// If those rules change, change these too: validation.test.ts uses the same cases as tests/unit/test_dms_storage.py.

export const MAX_NAME_CHARS = 64; // MAX_NAME_CHARS
export const SUPPORTED_SUFFIXES = [".md", ".pdf", ".txt"]; // SUPPORTED_SUFFIXES, sorted as in the server's message
export const FILE_TYPES_MESSAGE = `Only ${SUPPORTED_SUFFIXES.join(", ")} files can be uploaded.`;

// _FOLDER_NAME: letters and digits at both ends, with dots, hyphens and underscores allowed in between.
const FOLDER_NAME = /^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$/;
// _RESERVED: names Windows keeps for devices, with or without an extension.
const RESERVED = /^(CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])(\..*)?$/i;
// The characters Python's str.strip() removes (its whitespace), which differ slightly from String.prototype.trim().
const PY_SPACE = "[\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]";
const PY_STRIP = new RegExp(`^${PY_SPACE}+|${PY_SPACE}+$`, "g");

/** Python's str.strip(): what the server does to product, version and type before checking them. */
export function pyStrip(value: string): string {
  return value.replace(PY_STRIP, "");
}

/** The problem with a product, version or type value, or null if the server will accept it. `label` names the field. */
export function folderNameProblem(value: string, label: string): string | null {
  const trimmed = pyStrip(value);
  if (!trimmed) return `${label} is required.`;
  if (trimmed.length > MAX_NAME_CHARS || !FOLDER_NAME.test(trimmed) || trimmed.includes("..") || RESERVED.test(trimmed)) {
    return (
      `${label} must be 1-${MAX_NAME_CHARS} letters, digits, dots, hyphens or underscores, ` +
      "starting and ending with a letter or digit (for example 16.2 or release-notes)."
    );
  }
  return null;
}

// The tag lists' own rules (src/rag/dms/tags.py: check_category_key() and check_label(); a value's code follows
// folderNameProblem()). validation.test.ts uses the same cases as tests/unit/test_tags.py.
export const MAX_KEY_CHARS = 32;
export const MAX_LABEL_CHARS = 64;
const CATEGORY_KEY = /^[a-z][a-z0-9-]{0,31}$/;
const BUILTIN_CATEGORY_KEYS = ["product", "version", "doc_type"];

/** The problem with a new category's key, or null. */
export function categoryKeyProblem(value: string): string | null {
  const key = pyStrip(value);
  if (BUILTIN_CATEGORY_KEYS.includes(key)) return `'${key}' is a built-in category.`;
  if (!CATEGORY_KEY.test(key)) {
    return `The key must be 1-${MAX_KEY_CHARS} lowercase letters, digits or hyphens, starting with a letter (for example module).`;
  }
  return null;
}

/** A label as the server stores it: trimmed, inner runs of spaces made one. */
export function cleanLabel(value: string): string {
  return value.split(/\s+/).filter(Boolean).join(" ");
}

/** The problem with a category's or value's label, or null. */
export function labelProblem(value: string): string | null {
  const label = cleanLabel(value);
  return label && label.length <= MAX_LABEL_CHARS ? null : `The label must be 1-${MAX_LABEL_CHARS} characters.`;
}

/**
 * The extension the server will see for a file name: the last path part, reduced to ASCII the way the server does
 * it (NFKD, then dropping what has no ASCII form), then everything after the last dot, lower-cased.
 */
export function uploadSuffix(filename: string): string {
  const last = filename.replaceAll("\\", "/").split("/").pop() ?? "";
  const ascii = last.normalize("NFKD").replace(/[^\x00-\x7f]/g, "");
  const dot = ascii.lastIndexOf(".");
  return dot === -1 ? "" : `.${ascii.slice(dot + 1).toLowerCase()}`;
}

/** The problem with a chosen file, or null. The size limit is left to the server (it is configurable there). */
export function fileProblem(file: File | null): string | null {
  if (!file) return "Choose a file to upload.";
  if (!SUPPORTED_SUFFIXES.includes(uploadSuffix(file.name))) return FILE_TYPES_MESSAGE;
  if (file.size === 0) return "The file is empty.";
  return null;
}
