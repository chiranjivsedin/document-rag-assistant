import { describe, expect, it } from "vitest";

import { citedNumbers, splitCitations } from "./citations";

describe("splitCitations", () => {
  it("splits text and citations, including adjacent ones", () => {
    expect(splitCitations("Desktop is deprecated [1][2]. Use Web [3].", 3)).toEqual([
      { text: "Desktop is deprecated " },
      { cite: 1 },
      { cite: 2 },
      { text: ". Use Web " },
      { cite: 3 },
      { text: "." },
    ]);
  });

  it("leaves numbers outside the source list as plain text", () => {
    expect(splitCitations("See [0] and [4] but [2].", 3)).toEqual([{ text: "See [0] and [4] but " }, { cite: 2 }, { text: "." }]);
  });

  it("returns the whole text when there are no citations", () => {
    expect(splitCitations("No citations here.", 2)).toEqual([{ text: "No citations here." }]);
    expect(splitCitations("", 2)).toEqual([]);
  });
});

describe("citedNumbers", () => {
  it("collects each valid cited number once", () => {
    expect([...citedNumbers("A [1]. B [1][3]. C [9].", 3)].sort()).toEqual([1, 3]);
  });
});
