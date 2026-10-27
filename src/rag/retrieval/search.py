"""Retrieve the chunks most relevant to a question, optionally limited to a product and/or version."""

from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStore


def retrieve(
    store: VectorStore,
    question: str,
    k: int = 5,
    version: str | None = None,
    product: str | None = None,
) -> list[Document]:
    """The k closest chunks, closest first, each with its cosine distance in metadata["distance"]
    (0 = same meaning; larger = less related)."""
    filters = {name: value for name, value in (("version", version), ("product", product)) if value}
    results = store.similarity_search_with_score(question, k=k, filter=filters or None)
    return [
        Document(page_content=doc.page_content, metadata={**doc.metadata, "distance": distance})
        for doc, distance in results
    ]
