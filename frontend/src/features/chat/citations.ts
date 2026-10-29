// The model cites passages by their number in the prompt (see ANSWER_PROMPT in src/rag/generation/prompts.py).
// It is asked for [1] or [2][3], but small models also write [1, 2], [2,3], [1-3] or [ 1 ]; all of these map to
// the numbered sources. Only numbers of sources that were actually retrieved become citations.

export type AnswerPart = { text: string } | { cite: number };

const GROUP = /\[\s*(\d+(?:\s*(?:,|-|–)\s*\d+)*)\s*\]/g;
const MAX_RANGE = 20; // "[1-500]" is not a citation range worth expanding

/** The numbers in one bracket group: "1, 2" → [1, 2]; "1-3" → [1, 2, 3]. */
function expand(group: string): number[] {
  const numbers: number[] = [];
  for (const item of group.split(",")) {
    const [from, to] = item.split(/-|–/).map((n) => Number(n.trim()));
    if (to === undefined) numbers.push(from);
    else if (to >= from && to - from < MAX_RANGE) for (let n = from; n <= to; n++) numbers.push(n);
    else numbers.push(from, to);
  }
  return numbers;
}

/**
 * Split an answer into text and citation parts. `sourceNumbers` are the numbers of the retrieved sources; a cited
 * number without a source stays visible as plain text, e.g. "[7]".
 */
export function splitCitations(answer: string, sourceNumbers: ReadonlySet<number>): AnswerPart[] {
  const parts: AnswerPart[] = [];
  const pushText = (text: string) => {
    if (!text) return;
    const previous = parts.at(-1);
    if (previous && "text" in previous) previous.text += text;
    else parts.push({ text });
  };
  let last = 0;
  for (const match of answer.matchAll(GROUP)) {
    const numbers = expand(match[1]);
    if (!numbers.some((n) => sourceNumbers.has(n))) continue; // not a citation of ours: leave the text as written
    pushText(answer.slice(last, match.index));
    for (const n of numbers) {
      if (sourceNumbers.has(n)) parts.push({ cite: n });
      else pushText(`[${n}]`);
    }
    last = match.index + match[0].length;
  }
  pushText(answer.slice(last));
  return parts;
}

/** The source numbers an answer actually cites. */
export function citedNumbers(answer: string, sourceNumbers: ReadonlySet<number>): Set<number> {
  return new Set(splitCitations(answer, sourceNumbers).flatMap((part) => ("cite" in part ? [part.cite] : [])));
}
