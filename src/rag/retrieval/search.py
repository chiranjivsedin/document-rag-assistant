"""Retrieve the chunks most relevant to a question, optionally limited to a product and/or version."""

from dataclasses import dataclass

from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStore

# With a preference, this many candidates per requested passage are fetched to choose from.
CANDIDATES_PER_RESULT = 4


@dataclass(frozen=True)
class Preference:
    """Keep up to `slots` of the k passages for chunks of `doc_type` (e.g. release notes, which a few large
    guides on the same topics would otherwise crowd out), but only chunks at most `max_gap` further from the
    question than the closest candidate, so a clearly weaker match never replaces a strong one."""

    doc_type: str
    slots: int
    max_gap: float


def retrieve(
    store: VectorStore,
    question: str,
    k: int = 5,
    version: str | None = None,
    product: str | None = None,
    prefer: Preference | None = None,
) -> list[Document]:
    """The k closest chunks, closest first, each with its cosine distance in metadata["distance"]
    (0 = same meaning; larger = less related). With `prefer`, see choose()."""
    filters = {name: value for name, value in (("version", version), ("product", product)) if value}
    active = prefer is not None and prefer.slots > 0
    fetch = k * CANDIDATES_PER_RESULT if active else k
    results = store.similarity_search_with_score(question, k=fetch, filter=filters or None)
    candidates = [
        Document(page_content=doc.page_content, metadata={**doc.metadata, "distance": distance})
        for doc, distance in results
    ]
    return choose(candidates, k, prefer) if active else candidates[:k]


def choose(candidates: list[Document], k: int, prefer: Preference) -> list[Document]:
    """The k closest candidates (given closest first), except that when fewer than `prefer.slots` of them are
    of `prefer.doc_type`, the closest further ones of that type within `prefer.max_gap` of the best distance
    take the places of the furthest others. Returned closest first."""
    top = candidates[:k]
    if not top:
        return top
    preferred = [doc for doc in top if doc.metadata.get("doc_type") == prefer.doc_type]
    limit = top[0].metadata["distance"] + prefer.max_gap
    missing = min(prefer.slots, k) - len(preferred)
    extra = [doc for doc in candidates[k:]
             if doc.metadata.get("doc_type") == prefer.doc_type and doc.metadata["distance"] <= limit][:max(missing, 0)]
    if not extra:
        return top
    others = [doc for doc in top if doc.metadata.get("doc_type") != prefer.doc_type]
    kept = others[:len(others) - len(extra)]
    return sorted(preferred + kept + extra, key=lambda doc: doc.metadata["distance"])
