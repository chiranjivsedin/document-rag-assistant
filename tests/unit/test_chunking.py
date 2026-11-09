import pytest
from langchain_core.documents import Document

from rag.ingestion.chunking import find_headings, is_heading, section_at, split_documents

META = {"source": "documentum/16.2/release-notes/notes.pdf", "version": "16.2", "page": 4}


def paragraphs(count: int) -> str:
    return "\n\n".join(f"Paragraph {i}: " + "feature details " * 8 for i in range(count))


def test_short_text_stays_one_chunk():
    chunks = split_documents([Document(page_content="Webtop is deprecated.", metadata=META)], 1000, 150)
    assert len(chunks) == 1
    assert chunks[0].page_content == "Webtop is deprecated."
    assert chunks[0].metadata == {**META, "chunk": 0}


def test_long_text_is_split_within_size_and_keeps_metadata():
    chunks = split_documents([Document(page_content=paragraphs(20), metadata=META)], 300, 50)
    assert len(chunks) > 1
    assert all(len(c.page_content) <= 300 for c in chunks)
    assert [c.metadata["chunk"] for c in chunks] == list(range(len(chunks)))
    assert all(c.metadata["source"] == META["source"] and c.metadata["page"] == 4 for c in chunks)


def test_splits_on_paragraph_boundaries_when_possible():
    chunks = split_documents([Document(page_content=paragraphs(6), metadata=META)], 300, 0)
    assert all(c.page_content.startswith("Paragraph") for c in chunks)


def test_overlap_repeats_text_between_neighbouring_chunks():
    text = " ".join(f"word{i}" for i in range(400))
    chunks = split_documents([Document(page_content=text, metadata=META)], 200, 60)
    first_tail = chunks[0].page_content.split()[-3:]
    assert " ".join(first_tail) in chunks[1].page_content


def test_chunk_numbers_run_through_a_file_and_restart_per_file():
    docs = [
        Document(page_content=paragraphs(10), metadata={**META, "page": 1}),
        Document(page_content=paragraphs(10), metadata={**META, "page": 2}),
        Document(page_content=paragraphs(10), metadata={**META, "source": "other.pdf", "page": 1}),
    ]
    chunks = split_documents(docs, 300, 50)
    first_file = [c.metadata["chunk"] for c in chunks if c.metadata["source"] == META["source"]]
    assert first_file == list(range(len(first_file)))
    assert [c.metadata["chunk"] for c in chunks if c.metadata["source"] == "other.pdf"][0] == 0


def test_does_not_modify_input_metadata():
    doc = Document(page_content=paragraphs(10), metadata=dict(META))
    split_documents([doc], 300, 50)
    assert doc.metadata == META


def test_no_documents_gives_no_chunks():
    assert split_documents([], 1000, 150) == []


# Pages of one file are chunked as one text, and chunks carry their section. The fixtures are the page breaks
# from the live test on OpenText Documentum 23.4 release notes, where the answer sat on the page after its heading.

SERVER_PAGE_2 = """1 Introduction
These Release Notes provide an overview of Documentum Server 23.4.
2 About Documentum Server 23.4
This section provides an overview of Documentum Server 23.4.
Documentum Foundation Services (DFS) is no longer installed with Documentum Server.
2.1 New features
Documentum Server 23.4 includes the following new features."""
SERVER_PAGE_3 = """2.1.1 Multiple/Add-on repository provisioning
Documentum customers can add-on repositories to their cloud deployments.
2.1.2 Oracle Linux/Alpine Linux containers
Documentum Platform containers now ship with Linux Slim as the base operating system."""
THUMBNAIL_PAGE_7 = """7 Fixed issues
There are no fixed issues in this release.
8 Known issues
The following known issues exist in this release."""
THUMBNAIL_PAGE_8 = """Issue number Issue description
TSERVER-471 Thumbnail server installation is only supported on primary Documentum Server.
TSERVER-289 Thumbnail Server does not support failover scenario in a clustered Documentum Server setup."""


def pages(*texts: str, first: int = 2) -> list[Document]:
    return [Document(page_content=text, metadata={**META, "page": number}) for number, text in enumerate(texts, first)]


def test_a_heading_at_the_bottom_of_a_page_stays_with_the_list_on_the_next():
    chunks = split_documents(pages(SERVER_PAGE_2, SERVER_PAGE_3), 1000, 150)
    with_heading = [c for c in chunks if "includes the following new features" in c.page_content]
    assert any("2.1.1 Multiple/Add-on repository provisioning" in c.page_content for c in with_heading)


def test_a_chunk_starting_on_the_next_page_names_its_section():
    chunks = split_documents(pages(SERVER_PAGE_2, SERVER_PAGE_3), 200, 0)
    first_on_page_3 = next(c for c in chunks if "2.1.1 Multiple/Add-on" in c.page_content)
    assert first_on_page_3.metadata["page"] == 3
    assert first_on_page_3.page_content.startswith("Section: 2 About Documentum Server 23.4 > 2.1 New features\n")


def test_a_table_after_a_page_break_names_its_section():
    # "What are the known issues?" never retrieved page 8: nothing on it said "known issues"
    chunks = split_documents(pages(THUMBNAIL_PAGE_7, THUMBNAIL_PAGE_8, first=7), 200, 0)
    table = next(c for c in chunks if "TSERVER-289" in c.page_content)
    assert table.page_content.startswith("Section: 8 Known issues\n")
    assert table.metadata["page"] == 8


def test_page_is_where_the_chunk_starts():
    chunks = split_documents(pages("alpha " * 100, "beta " * 100, first=1), 200, 0)
    assert {c.metadata["page"] for c in chunks} == {1, 2}
    for chunk in chunks:
        assert chunk.page_content.startswith({1: "alpha", 2: "beta"}[chunk.metadata["page"]])


def test_text_without_numbered_headings_gets_no_prefix():
    chunks = split_documents([Document(page_content=paragraphs(10), metadata=META)], 300, 50)
    assert not any(c.page_content.startswith("Section:") for c in chunks)


def test_a_chunk_starting_with_its_heading_lists_only_the_parents():
    text = "2 About X\nIntro.\n2.1 New features\n" + "Feature detail. " * 20
    headings = find_headings([text])
    assert section_at(headings, text.index("2.1 New features")) == "2 About X"
    assert section_at(headings, text.index("Feature detail.")) == "2 About X > 2.1 New features"
    assert section_at(headings, 0) == ""


def test_a_heading_replaces_its_level_and_below():
    text = "1 Intro\n1.1 History\n1.1.1 Details\n2 About\n2.1 New features\nBody"
    headings = find_headings([text])
    assert section_at(headings, text.index("Body")) == "2 About > 2.1 New features"


def test_table_of_contents_pages_have_no_headings():
    toc = ("Table of Contents\n1 Overview ....................  9\n2 Upgrade overview ............  11\n"
           "2.1 Upgrade and migration ..........  11\n3.5.4 Upgrade matrices for Documentum Server ... 27")
    body = "2.1 Upgrade and migration\nThis guide covers upgrade."
    headings = find_headings([toc, body])
    assert [line for _, _, line in headings] == ["2.1 Upgrade and migration"]
    assert headings[0][0] == len(toc) + 1  # offsets are in the pages joined by line breaks


@pytest.mark.parametrize("line", [
    "2.1 New features",
    "8.3 Known issues specific to Docker and Kubernetes",
    "2.1.1 Multiple/Add-on repository provisioning",
    "2.1.7 Improvements and new certifications in CE 23.4",
    "4.6.6 Removing log4j 1.x files",
])
def test_numbered_headings(line):
    assert is_heading(line, (2, 1))


@pytest.mark.parametrize("line", [
    "7.0 N Y Y Y Y Y Y",  # a version matrix row
    "6.7 SP2 Y Y Y Y",
    "0 None User has no special",  # a table row
    "1. Documentum Server",  # a numbered list item
    "2.1 New features ................ 7",  # a table-of-contents entry
    "3 servers are needed for this.",  # a sentence
    "2.1 The following features have been discontinued in this release:",
    "Documentum Server 23.4 Release Notes 3",
])
def test_lines_that_are_not_headings(line):
    assert is_heading(line, (2, 1)) is None


def test_a_single_number_heading_must_continue_the_numbering():
    assert is_heading("1 Introduction", None) == (1,)
    assert is_heading("7 Fixed issues", None) == (7,)  # the first heading may have any number
    assert is_heading("3 Downloads", (2, 2, 2)) == (3,)
    assert is_heading("8 Sysadmin User has System", (6, 7, 1)) is None  # a table row in the Fundamentals Guide
    assert is_heading("16 Superuser User has Superuser", (6, 7, 1)) is None
    assert is_heading("2 Browse The user can look at property", (6, 7, 1)) is None
