"""Tidy the [n] citations in a model's reply, and spot replies that aren't answers.

The prompt asks for one citation per passage, like [2]; small models also write [1][2], [1, 2], [ 1 ] or [1-3]
(the web UI reads all of these). Tidying only removes repeats; it never changes the words.
"""

import re

# One citation group: numbers separated by commas or a range dash, e.g. "[1]", "[1, 2]", "[1-3]".
GROUP = re.compile(r"\[\s*\d+(?:\s*(?:,|-|–)\s*\d+)*\s*\]")
# Groups that follow each other with only spaces between them, e.g. "[1][2]" or "[1] [1]".
RUN = re.compile(rf"{GROUP.pattern}(?:[ \t]*{GROUP.pattern})*")

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


def tidy_citations(text: str) -> str:
    """Drop a number already cited in the same place: "[1][1]", "[1] [1]" and "[1, 1]" become "[1]"."""
    return RUN.sub(_tidy_run, text)


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
