import { describe, expect, it } from "vitest";

import { categoryKeyProblem, fileProblem, folderNameProblem, labelProblem, pyStrip, uploadSuffix } from "./validation";

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

// The same cases as tests/unit/test_tags.py (rag.dms.tags: check_category_key(), check_label(), check_code()).
describe("tag rules (the server's rag.dms.tags)", () => {
  it.each([["module"], ["audience"], ["m"], ["os-family2"], [" module "]])("accepts the category key %j", (key) => {
    expect(categoryKeyProblem(key)).toBeNull();
  });

  it.each([[""], ["Module"], ["2module"], ["-x"], ["a_b"], ["a b"], ["a:b"], ["x".repeat(33)]])("refuses the key %j", (key) => {
    expect(categoryKeyProblem(key)).toBe(
      "The key must be 1-32 lowercase letters, digits or hyphens, starting with a letter (for example module).",
    );
  });

  it.each([["product"], ["version"], ["doc_type"]])("refuses the built-in key %j", (key) => {
    expect(categoryKeyProblem(key)).toBe(`'${key}' is a built-in category.`);
  });

  it("labels: 1-64 characters once spaces are trimmed and collapsed", () => {
    expect(labelProblem("  Release   notes ")).toBeNull();
    expect(labelProblem("x".repeat(64))).toBeNull();
    for (const bad of ["", "   ", "x".repeat(65)]) expect(labelProblem(bad)).toBe("The label must be 1-64 characters.");
  });

  it("codes follow the folder-name rule", () => {
    for (const bad of ["../x", "a b", "-a", "con", "."]) expect(folderNameProblem(bad, "The code")).not.toBeNull();
    expect(folderNameProblem("release-notes", "The code")).toBeNull();
  });
});
