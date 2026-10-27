from langchain_core.documents import Document

from rag.ingestion.chunking import split_documents

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


def test_chunk_numbers_restart_per_document():
    docs = [
        Document(page_content=paragraphs(10), metadata={**META, "page": 1}),
        Document(page_content=paragraphs(10), metadata={**META, "page": 2}),
    ]
    chunks = split_documents(docs, 300, 50)
    page_2 = [c for c in chunks if c.metadata["page"] == 2]
    assert page_2[0].metadata["chunk"] == 0


def test_does_not_modify_input_metadata():
    doc = Document(page_content=paragraphs(10), metadata=dict(META))
    split_documents([doc], 300, 50)
    assert doc.metadata == META


def test_no_documents_gives_no_chunks():
    assert split_documents([], 1000, 150) == []
