"""Tidy the [n] citations in a model's reply, and spot replies that aren't answers.

The prompt asks for one citation per passage, like [2]; small models also write [1][2], [1, 2], [ 1 ] or [1-3]
(the web UI reads all of these). Tidying repairs and removes citations; it never changes the words.
"""

import re

# One citation group: numbers separated by commas or a range dash, e.g. "[1]", "[1, 2]", "[1-3]".
GROUP = re.compile(r"\[\s*\d+(?:\s*(?:,|-|–)\s*\d+)*\s*\]")
# Groups that follow each other with only spaces between them, e.g. "[1][2]" or "[1] [1]".
RUN = re.compile(rf"{GROUP.pattern}(?:[ \t]*{GROUP.pattern})*")
# A group followed by more numbers and a second closing bracket: gemma3:4b wrote "... in version 23.4 [2], 4]."
# for "[2, 4]". Only this exact shape is repaired; any other stray bracket is left as written.
BROKEN_GROUP = re.compile(r"\[(\s*\d+(?:\s*(?:,|-|–)\s*\d+)*)\s*\]((?:\s*,\s*\d+)+)\s*\]")
# A group with the spaces before it, which go when the whole group is dropped.
SPACED_GROUP = re.compile(rf"[ \t]*{GROUP.pattern}")
# A section number in citation brackets, with the spaces before it: chunks start with "Section: 2.1 New features",
# and gemma3:4b then cited "[5.2.1.1]". No passage has such a number, so it goes like any other invalid citation.
SECTION_CITATION = re.compile(r"[ \t]*\[\s*\d+(?:\.\d+)+\s*\]")

# A sentence saying the documents lack something: "... is not stated / mentioned ...", "The passages do not
# mention ...", "There is no information about ...". English only, like the prompt.
NOT_STATED = re.compile(
    r"\b(?:(?:is|are|was|were)\s+not|isn't|aren't|wasn't|weren't)\s+(?:explicitly\s+)?"
    r"(?:stated|mentioned|provided|specified|given|included|listed|documented|described)\b"
    r"|\b(?:do|does|did)\s*(?:not|n't)\s+(?:explicitly\s+)?"
    r"(?:state|mention|provide|specify|say|include|list|contain|describe)\b"
    r"|\bno\s+(?:information|mention|details)\b",
    re.IGNORECASE,
)
# ...and that it is the documents, not the product, that lack it ("not provided in 1.0" is a fact).
DOCUMENTS = re.compile(r"\b(?:passages?|documents?|documentation|context|text|sources?|release notes)\b", re.IGNORECASE)


def has_no_words(text: str) -> bool:
    """True when the reply is empty or only citations and punctuation (gemma3:4b once answered just "[1]")."""
    return not re.search(r"\w", GROUP.sub("", text))


def only_says_not_stated(text: str) -> bool:
    """True when every sentence only says the documents don't contain the answer, e.g. "The release date of 2.0
    is not stated in the passages [1]." (gemma3:4b's wording instead of the exact "I don't know" reply).
    A reply that also states a fact is an answer."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", GROUP.sub("", text)) if re.search(r"\w", s)]
    return bool(sentences) and all(NOT_STATED.search(s) and DOCUMENTS.search(s) for s in sentences)


def cited_numbers(text: str) -> set[int]:
    """The passage numbers a reply cites: "[1]", "[1, 3]" and "[2-4]" (all of 2, 3, 4) all count."""
    numbers: set[int] = set()
    for group in GROUP.finditer(text):
        for item in group.group()[1:-1].split(","):
            ends = [int(n) for n in re.findall(r"\d+", item)]
            numbers.update(range(min(ends), max(ends) + 1))
    return numbers


def tidy_citations(text: str, passages: int | None = None) -> str:
    """Repair "[2], 4]" into "[2, 4]"; with the number of `passages`, drop citation numbers that match no passage
    ("[7]" with 5 passages; a group left empty goes with the spaces before it) and section numbers cited as
    passages ("[5.2.1.1]"); then drop a number already cited in the same place: "[1][1]", "[1] [1]" and "[1, 1]"
    become "[1]". Numbers are never changed."""
    text = BROKEN_GROUP.sub(_repair_group, text)
    if passages is not None:
        text = SECTION_CITATION.sub("", text)
        text = SPACED_GROUP.sub(lambda group: _in_range(group, passages), text)
    return RUN.sub(_tidy_run, text)


def _repair_group(match: re.Match) -> str:
    return f"[{match.group(1).strip()}, {', '.join(re.findall(r'\d+', match.group(2)))}]"


def _in_range(match: re.Match, passages: int) -> str:
    """The group with only numbers from 1 to `passages` (a range is cut to fit), or "" if none is left."""
    spaces, group = re.match(r"([ \t]*)(.*)", match.group(), re.DOTALL).groups()
    items = []
    for item in group[1:-1].split(","):
        ends = [int(n) for n in re.findall(r"\d+", item)]
        low, high = max(min(ends), 1), min(max(ends), passages)
        if low < high:
            items.append(f"{low}-{high}")
        elif low == high:
            items.append(str(low))
    if not items:
        return ""
    numbers = [int(n) for n in re.findall(r"\d+", group)]
    if all(1 <= n <= passages for n in numbers):
        return match.group()  # all valid: keep it as written
    return f"{spaces}[{', '.join(items)}]"


def _tidy_run(run: re.Match) -> str:
    seen: set[int] = set()
    kept = []
    for group in GROUP.finditer(run.group()):
        inside = group.group()[1:-1]
        if re.search(r"-|–", inside):  # a range: leave it as written
            kept.append(group.group())
            continue
        numbers = [n for n in dict.fromkeys(int(item) for item in inside.split(",")) if n not in seen]
        seen.update(numbers)
        if numbers:
            kept.append("[" + ", ".join(str(n) for n in numbers) + "]")
    if kept == [group.group() for group in GROUP.finditer(run.group())]:
        return run.group()  # nothing repeated: keep the original spacing
    return "".join(kept)
