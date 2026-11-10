"""Retrieve the chunks most relevant to a question, optionally limited to a product and/or version."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStore

# Passages given to the model when the caller doesn't say. 3, not 5: in a live test on the Documentum 23.4 documents
# gemma3:4b answered ~40% faster (about 70 s instead of 110 s on the laptop CPU) with the same or better answers,
# because reading the passages is most of its time.
DEFAULT_K = 3

# With a preference, or with hybrid search, this many candidates per requested passage are fetched (from each search)
# to choose from.
CANDIDATES_PER_RESULT = 4


@dataclass(frozen=True)
class Preference:
    """Keep up to `slots` of the k passages for chunks of `doc_type` (e.g. release notes, which a few large
    guides on the same topics would otherwise crowd out), but only chunks at most `max_gap` further from the
    question than the closest candidate, so a clearly weaker match never replaces a strong one."""

    doc_type: str
    slots: int
    max_gap: float


class KeywordSearch(Protocol):
    """Up to `limit` chunks matching the question's words within `filters`, best first, each with its cosine distance
    to `embedding` (the question's) in metadata["distance"]. See retrieval.keyword."""

    def __call__(self, question: str, embedding: list[float], filters: Mapping[str, str],
                 limit: int) -> list[Document]: ...


@dataclass(frozen=True)
class Hybrid:
    """Also search by keyword, and merge the two rankings with Reciprocal Rank Fusion (see fuse())."""

    keyword_search: KeywordSearch
    rrf_k: int = 10


def retrieve(
    store: VectorStore,
    question: str,
    k: int = DEFAULT_K,
    version: str | None = None,
    product: str | None = None,
    prefer: Preference | None = None,
    hybrid: Hybrid | None = None,
) -> list[Document]:
    """The k most relevant chunks, best first, each with its cosine distance in metadata["distance"]
    (0 = same meaning; larger = less related). Without `hybrid`, best = closest. With `hybrid`, the vector and
    keyword rankings are fused (see fuse()) and each chunk says which search found it in metadata["found_by"]
    ("vector", "keyword" or "both"). With `prefer`, see choose()."""
    filters = {name: value for name, value in (("version", version), ("product", product)) if value}
    active = prefer is not None and prefer.slots > 0
    if hybrid is None:
        fetch = k * CANDIDATES_PER_RESULT if active else k
        results = store.similarity_search_with_score(question, k=fetch, filter=filters or None)
        candidates = _with_distance(results)
    else:
        fetch = k * CANDIDATES_PER_RESULT  # deeper lists, so a passage only one search finds can still rank
        embedding = store.embeddings.embed_query(question)  # once, for both searches
        results = store.similarity_search_with_score_by_vector(embedding, k=fetch, filter=filters or None)
        vector_hits = _with_distance(results)
        keyword_hits = hybrid.keyword_search(question, embedding, filters, fetch)
        candidates = fuse([vector_hits, keyword_hits], hybrid.rrf_k)
        by_vector = {_identity(doc) for doc in vector_hits}
        by_keyword = {_identity(doc) for doc in keyword_hits}
        for doc in candidates:  # shown by scripts/ask.py, to see what the keyword search added
            key = _identity(doc)
            doc.metadata["found_by"] = "both" if key in by_vector and key in by_keyword else (
                "vector" if key in by_vector else "keyword")
    return choose(candidates, k, prefer) if active else candidates[:k]


def _with_distance(results: Sequence[tuple[Document, float]]) -> list[Document]:
    return [Document(id=doc.id, page_content=doc.page_content, metadata={**doc.metadata, "distance": distance})
            for doc, distance in results]


def fuse(rankings: Sequence[Sequence[Document]], rrf_k: int) -> list[Document]:
    """Reciprocal Rank Fusion: each passage scores the sum, over the rankings it appears in, of 1 / (rrf_k + rank),
    rank counting from 1; best score first, ties going to the smaller distance. A passage keeps the Document
    (and distance) of the first ranking it appears in. Passages are the same when their ids are (or, without ids,
    their source and text).

    rrf_k is kept small (10 by default, against the usual 60) because the lists are short (4 × k): with 60, any
    passage found by both searches, however far down, outranks the top hit of either one alone, so a passage only
    the keyword search finds (a fact stated in a chunk about something else) could never make the k."""
    scores: dict[object, float] = {}
    first: dict[object, Document] = {}
    for ranking in rankings:
        for rank, doc in enumerate(ranking, 1):
            key = _identity(doc)
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
            first.setdefault(key, doc)
    order = sorted(scores, key=lambda key: (-scores[key], first[key].metadata["distance"]))
    return [first[key] for key in order]


def _identity(doc: Document) -> object:
    return doc.id if doc.id else (doc.metadata.get("source"), doc.page_content)


def choose(candidates: list[Document], k: int, prefer: Preference) -> list[Document]:
    """The first k candidates (given best first), except that when fewer than `prefer.slots` of them are of
    `prefer.doc_type`, the best further ones of that type within `prefer.max_gap` of the closest candidate's
    distance take the places of the last others. Returned in candidate order."""
    top = candidates[:k]
    if not top:
        return top
    preferred = [doc for doc in top if doc.metadata.get("doc_type") == prefer.doc_type]
    limit = min(doc.metadata["distance"] for doc in candidates) + prefer.max_gap
    missing = min(prefer.slots, k) - len(preferred)
    extra = [doc for doc in candidates[k:]
             if doc.metadata.get("doc_type") == prefer.doc_type and doc.metadata["distance"] <= limit][:max(missing, 0)]
    if not extra:
        return top
    others = [doc for doc in top if doc.metadata.get("doc_type") != prefer.doc_type]
    kept = {id(doc) for doc in preferred + others[:len(others) - len(extra)] + extra}
    return [doc for doc in candidates if id(doc) in kept]
