import { describe, expect, it } from "vitest";

import { fileProblem, folderNameProblem, pyStrip, uploadSuffix } from "./validation";

// The same cases as the server's own tests (tests/unit/test_dms_storage.py), so both sides agree.
const RULE =
  "must be 1-64 letters, digits, dots, hyphens or underscores, starting and ending with a letter or digit " +
  "(for example 16.2 or release-notes).";

describe("folderNameProblem (the server's check_folder_name)", () => {
  it.each([["documentum"], ["16.2"], ["release-notes"], ["v_1"], ["  Acme  "], ["x"], ["a".repeat(64)], ["COM10"], ["console"]])(
    "accepts %j",
    (value) => {
      expect(folderNameProblem(value, "Version")).toBeNull();
    },
  );

  it.each([["", "is required."], ["   ", "is required."], ["\t\n", "is required."]])("requires a value: %j", (value, message) => {
    expect(folderNameProblem(value, "Product")).toBe(`Product ${message}`);
  });

  it.each([
    [".."], ["../x"], ["a/b"], ["a\\b"], [".hidden"], ["x."], ["-x"], ["16..2"], ["a b"], ["café"], ["CON"], ["lpt1"],
    ["nul.txt"], ["Aux"], ["com\u00b9"], ["a".repeat(65)],
  ])("refuses %j", (value) => {
    expect(folderNameProblem(value, "Version")).toBe(`Version ${RULE}`);
  });
});

describe("pyStrip (Python's str.strip)", () => {
  it("removes what Python treats as whitespace, which trim() does not all cover", () => {
    expect(pyStrip("\x1c\x1facme\x85 ")).toBe("acme");
    expect(pyStrip("\u3000acme\u2029")).toBe("acme");
  });

  it("keeps what Python keeps, even where trim() would remove it", () => {
    expect(pyStrip("\ufeffacme")).toBe("\ufeffacme"); // so the check then refuses it, as the server does
    expect(folderNameProblem("\ufeffacme", "Product")).toBe(`Product ${RULE}`);
  });
});

describe("uploadSuffix and fileProblem (the server's clean_filename)", () => {
  it.each([
    ["notes.pdf", ".pdf"],
    ["Notes (Final) v2.PDF", ".pdf"],
    ["C:\\fakepath\\notes.md", ".md"],
    ["../../etc/passwd.txt", ".txt"],
    ["résumé.md", ".md"],
    ["notes.\uff50\uff44\uff46", ".pdf"], // full-width letters: NFKD turns them into ASCII on the server too
    ["notes", ""],
    ["notes.pdf.exe", ".exe"],
    ["", ""],
  ])("%j has the suffix %j", (name, suffix) => {
    expect(uploadSuffix(name)).toBe(suffix);
  });

  const file = (name: string, content = "text") => new File([content], name);

  it.each([["notes.docx"], ["notes"], ["notes.pdf.exe"], ["archive.zip"]])("refuses the type of %j", (name) => {
    expect(fileProblem(file(name))).toBe("Only .md, .pdf, .txt files can be uploaded.");
  });

  it("asks for a file, refuses an empty one, and accepts a supported one", () => {
    expect(fileProblem(null)).toBe("Choose a file to upload.");
    expect(fileProblem(file("notes.md", ""))).toBe("The file is empty.");
    expect(fileProblem(file("NOTES.PDF"))).toBeNull();
  });
});
