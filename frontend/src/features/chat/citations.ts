// The model cites passages as [1], [2][3], ... (see ANSWER_PROMPT in src/rag/generation/prompts.py).

export type AnswerPart = { text: string } | { cite: number };

/** Split an answer into text and citation parts. Only numbers 1..sourceCount become citations; others stay text. */
export function splitCitations(answer: string, sourceCount: number): AnswerPart[] {
  const parts: AnswerPart[] = [];
  let last = 0;
  for (const match of answer.matchAll(/\[(\d+)\]/g)) {
    const number = Number(match[1]);
    if (number < 1 || number > sourceCount) continue;
    if (match.index > last) parts.push({ text: answer.slice(last, match.index) });
    parts.push({ cite: number });
    last = match.index + match[0].length;
  }
  if (last < answer.length) parts.push({ text: answer.slice(last) });
  return parts;
}

/** The source numbers an answer actually cites. */
export function citedNumbers(answer: string, sourceCount: number): Set<number> {
  return new Set(splitCitations(answer, sourceCount).flatMap((part) => ("cite" in part ? [part.cite] : [])));
}
