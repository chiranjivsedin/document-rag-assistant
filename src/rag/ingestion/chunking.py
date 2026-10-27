"""Split document text into chunks for embedding."""

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


def split_documents(docs: list[Document], chunk_size: int, chunk_overlap: int) -> list[Document]:
    """Split each document into chunks that keep its metadata, numbered from 0 within that document."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks = []
    for doc in docs:
        for number, text in enumerate(splitter.split_text(doc.page_content)):
            chunks.append(Document(page_content=text, metadata={**doc.metadata, "chunk": number}))
    return chunks
