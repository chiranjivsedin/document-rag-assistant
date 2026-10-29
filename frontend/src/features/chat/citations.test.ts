import { describe, expect, it } from "vitest";

import { citedNumbers, splitCitations, type AnswerPart } from "./citations";

const sources = (...numbers: number[]) => new Set(numbers);
const cites = (parts: AnswerPart[]) => parts.flatMap((part) => ("cite" in part ? [part.cite] : []));
const text = (parts: AnswerPart[]) => parts.map((part) => ("text" in part ? part.text : `<${part.cite}>`)).join("");

describe("splitCitations: the forms the prompt asks for", () => {
  it("splits text and citations, including adjacent ones", () => {
    expect(splitCitations("Desktop is deprecated [1][2]. Use Web [3].", sources(1, 2, 3))).toEqual([
      { text: "Desktop is deprecated " },
      { cite: 1 },
      { cite: 2 },
      { text: ". Use Web " },
      { cite: 3 },
      { text: "." },
    ]);
  });

  it("returns the whole text when there are no citations", () => {
    expect(splitCitations("No citations here.", sources(1, 2))).toEqual([{ text: "No citations here." }]);
    expect(splitCitations("", sources(1, 2))).toEqual([]);
  });
});

describe("splitCitations: grouped forms small models produce (regression)", () => {
  it.each([
    ["[1, 2]", [1, 2]],
    ["[1,2]", [1, 2]],
    ["[ 2 ]", [2]],
    ["[1-3]", [1, 2, 3]],
    ["[1–3]", [1, 2, 3]],
    ["[1, 3-4]", [1, 3, 4]],
  ])("%s cites %j", (written, expected) => {
    const parts = splitCitations(`Deprecated ${written}.`, sources(1, 2, 3, 4));
    expect(cites(parts)).toEqual(expected);
    expect(text(parts)).toBe(`Deprecated ${expected.map((n) => `<${n}>`).join("")}.`);
  });

  it("keeps numbers without a retrieved source as visible text, even inside a group", () => {
    expect(text(splitCitations("See [2, 7] and [7].", sources(1, 2)))).toBe("See <2>[7] and [7].");
  });

  it("does not expand absurd ranges", () => {
    expect(cites(splitCitations("[1-500]", sources(1, 500)))).toEqual([1, 500]);
  });

  it("leaves bracketed text that isn't a citation alone", () => {
    expect(splitCitations("Use [Ctrl] or [a, b] or [0].", sources(1))).toEqual([{ text: "Use [Ctrl] or [a, b] or [0]." }]);
  });
});

describe("splitCitations: mapping is by source number, not by list length (regression)", () => {
  it("links only numbers that exist among the retrieved sources", () => {
    // The API numbers sources 1..k; if a list ever had gaps, [3] must not link to a missing source.
    const parts = splitCitations("A [1]. B [3]. C [4].", sources(1, 2, 4));
    expect(cites(parts)).toEqual([1, 4]);
    expect(text(parts)).toBe("A <1>. B [3]. C <4>.");
  });

  it("links nothing when no sources were retrieved", () => {
    expect(cites(splitCitations("A [1].", sources()))).toEqual([]);
  });
});

describe("citedNumbers", () => {
  it("collects each valid cited number once, from every form", () => {
    expect([...citedNumbers("A [1]. B [1][3]. C [2-3]. D [9].", sources(1, 2, 3))].sort()).toEqual([1, 2, 3]);
  });
});
