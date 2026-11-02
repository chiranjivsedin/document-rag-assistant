import { describe, expect, it } from "vitest";

import type { DocumentRecord } from "../../api/types";
import { distinct, formatSize, isWaiting, matchesSearch } from "./format";

describe("formatSize", () => {
  it.each([
    [0, "0 B"],
    [512, "512 B"],
    [2_458, "2.4 KB"],
    [52_428_800, "50 MB"],
    [13_736_000, "13 MB"],
    [3 * 1024 ** 3, "3.0 GB"],
  ])("%i bytes is %s", (bytes, text) => {
    expect(formatSize(bytes)).toBe(text);
  });
});

const doc = (overrides: Partial<DocumentRecord>): DocumentRecord => ({
  id: "1",
  source: "documentum/16.2/release-notes/notes.pdf",
  filename: "Release Notes.pdf",
  product: "documentum",
  version: "16.2",
  doc_type: "release-notes",
  content_type: "application/pdf",
  size_bytes: 1,
  file_hash: "a".repeat(64),
  origin: "upload",
  status: "indexed",
  error: null,
  chunk_count: 3,
  added_at: "2026-11-02T09:00:00Z",
  indexed_at: "2026-11-02T09:01:00Z",
  ...overrides,
});

describe("matchesSearch", () => {
  it.each([[""], ["  "], ["release"], ["NOTES"], ["16.2"], ["documentum/16"], ["release-notes"]])("matches %j", (search) => {
    expect(matchesSearch(doc({}), search)).toBe(true);
  });

  it("doesn't match other text, and copes with missing folders", () => {
    expect(matchesSearch(doc({}), "acme")).toBe(false);
    expect(matchesSearch(doc({ product: null, version: null, doc_type: null, source: "readme.md", filename: "readme.md" }), "16.2")).toBe(false);
  });
});

describe("isWaiting and distinct", () => {
  it("treats pending and processing as waiting", () => {
    expect(["pending", "processing", "indexed", "failed"].map((status) => isWaiting(doc({ status } as Partial<DocumentRecord>))))
      .toEqual([true, true, false, false]);
  });

  it("lists distinct values in natural order, without blanks", () => {
    expect(distinct(["16.2", null, "9.1", "16.2", "", "23.4"])).toEqual(["9.1", "16.2", "23.4"]);
  });
});
