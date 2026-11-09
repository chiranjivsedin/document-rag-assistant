"""Split document text into chunks for embedding.

All pages of one file are cut as one continuous text, so a chunk can run across a page break: a section heading
and its intro sentence at the bottom of one page stay together with the list or table that starts the next.
Each chunk keeps the metadata (including `page`) of the page it starts on.

Each chunk also starts with the numbered section headings it falls under, e.g.
"Section: 2 About Documentum Server 23.4 > 2.1 New features", so a chunk from the middle of a section (say,
the top of the next page) still says what the section is about, both to the embedding and to the model.
"""

import re
from bisect import bisect_right
from itertools import groupby

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

# A numbered heading on a line of its own: "2.1 New features", "8 Known issues", "2.1.1 Multiple/Add-on ...".
# Deliberately strict, so ordinary lines aren't taken for headings (see is_heading()): at most six levels of one-
# or two-digit numbers, then a title that starts with a capital letter and is at most 120 characters. "1. Install"
# (a numbered list item) doesn't match.
HEADING = re.compile(r"(\d{1,2}(?:\.\d{1,2}){0,5})[ \t]+([A-Z][^\n]{0,119})")
# Table-of-contents entries ("2.1 New features ........ 7"). A page with TOC_LINES of them is a table of contents,
# and none of its lines is a heading (some entries there are cut short or wrapped and have no dots).
DOT_LEADER = re.compile(r"\.{4,}|(?:\. ){3,}")
TOC_LINES = 3
SECTION_PREFIX = "Section: "


def split_documents(docs: list[Document], chunk_size: int, chunk_overlap: int) -> list[Document]:
    """Split each file's documents (its pages, in order) into chunks numbered from 0 within that file.

    Consecutive documents with the same `source` are one file. Each chunk gets the metadata of the page it
    starts on plus its `chunk` number, and is prefixed with the section it belongs to when the file has numbered
    headings (see the module docstring)."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks = []
    for _, pages in groupby(docs, key=lambda doc: doc.metadata.get("source")):
        chunks.extend(_split_file(list(pages), splitter))
    return chunks


def _split_file(pages: list[Document], splitter: RecursiveCharacterTextSplitter) -> list[Document]:
    # Pages are joined with a single line break, so a page break is no stronger a place to split than a line.
    page_starts, offset = [], 0
    for page in pages:
        page_starts.append(offset)
        offset += len(page.page_content) + 1
    text = "\n".join(page.page_content for page in pages)
    headings = find_headings([page.page_content for page in pages])

    chunks, search_from = [], 0
    for number, piece in enumerate(splitter.split_text(text)):
        start = text.find(piece, search_from)
        if start < 0:  # not expected: the splitter's pieces are slices of the text
            start = search_from
        search_from = start + 1
        page = pages[bisect_right(page_starts, start) - 1]
        section = section_at(headings, start)
        content = f"{SECTION_PREFIX}{section}\n{piece}" if section else piece
        chunks.append(Document(page_content=content, metadata={**page.metadata, "chunk": number}))
    return chunks


def find_headings(pages: list[str]) -> list[tuple[int, tuple[int, ...], str]]:
    """(offset, number parts, heading line) for each numbered heading in the pages joined by line breaks."""
    headings: list[tuple[int, tuple[int, ...], str]] = []
    page_start = 0
    for page in pages:
        lines = list(re.finditer(r"[^\n]+", page))
        if sum(bool(DOT_LEADER.search(line.group())) for line in lines) < TOC_LINES:
            for line in lines:
                numbers = is_heading(line.group().strip(), headings[-1][1] if headings else None)
                if numbers:
                    indent = len(line.group()) - len(line.group().lstrip())
                    headings.append((page_start + line.start() + indent, numbers, " ".join(line.group().split())))
        page_start += len(page) + 1
    return headings


def is_heading(line: str, previous: tuple[int, ...] | None) -> tuple[int, ...] | None:
    """The heading's number parts if `line` is a numbered heading, given the previous heading's numbers.

    Besides matching HEADING, the title must contain a lowercase letter and not end like a sentence or a list
    item, and no number part may be 0: that rules out table rows such as "7.0 N Y Y Y" or "0 None". A
    single-number heading ("8 Known issues") after another heading must also continue the numbering: a table
    row such as "16 Superuser User has Superuser" within section 6.7.1 fits HEADING but not the sequence.
    Rejecting a real heading costs little: the next accepted one replaces it.
    """
    match = HEADING.fullmatch(line)
    if (match is None or DOT_LEADER.search(line) or not re.search(r"[a-z]", match.group(2))
            or match.group(2).rstrip().endswith((".", ",", ";", ":"))):
        return None
    numbers = tuple(int(part) for part in match.group(1).split("."))
    if 0 in numbers:
        return None
    if len(numbers) == 1 and previous is not None and numbers[0] != previous[0] + 1:
        return None
    return numbers


def section_at(headings: list[tuple[int, tuple[int, ...], str]], offset: int) -> str:
    """The headings in force at `offset`, outermost first, e.g. "2 About X > 2.1 New features".

    A heading replaces the ones at its own level and below; its parents (whose numbers start its own) stay.
    A heading starting exactly at `offset` isn't included: the chunk's text shows it already.
    """
    trail: list[tuple[tuple[int, ...], str]] = []
    for position, numbers, line in headings:
        if position >= offset:
            break
        trail = [(n, t) for n, t in trail if len(n) < len(numbers) and numbers[:len(n)] == n] + [(numbers, line)]
    return " > ".join(line for _, line in trail)
