"""Shared steps behind the entry points (the CLI scripts now, the HTTP API next).

Builds the vector store and chat model once, runs an ingest against what the database already
holds, and answers a question, so each entry point doesn't repeat this glue.
"""

from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.vectorstores import VectorStore

from rag.config import Settings
from rag.database import indexed_files
from rag.generation.llm import Answer, answer
from rag.generation.prompts import NO_ANSWER
from rag.ingestion.pipeline import IngestReport, ingest
from rag.providers import get_chat_model, get_embeddings, get_keyword_search, get_vector_store
from rag.retrieval.search import DEFAULT_K, Hybrid, KeywordSearch, Preference, retrieve


class TagLists(Protocol):
    """What chat needs from the tag lists (rag.dms.tags.TagRepository)."""

    def sources_with_tags(self, selected: Mapping[str, Sequence[str]]) -> set[str]: ...


@dataclass(frozen=True)
class Services:
    settings: Settings
    store: VectorStore
    chat_model: BaseChatModel | None  # None when built only for ingesting
    keyword_search: KeywordSearch | None = None  # None = vector search only (HYBRID_SEARCH off)


def build_services(settings: Settings, with_chat_model: bool = True) -> Services:
    """Connect to the vector store (creating its table on first use) and set up the chat model and, with
    HYBRID_SEARCH, the keyword search."""
    store = get_vector_store(settings, get_embeddings(settings))
    chat_model = get_chat_model(settings) if with_chat_model else None
    return Services(settings=settings, store=store, chat_model=chat_model,
                    keyword_search=get_keyword_search(settings))


def run_ingest(services: Services, documents_dir: Path) -> IngestReport:
    """Bring the store in line with documents_dir; files already stored unchanged are skipped."""
    settings = services.settings
    indexed = indexed_files(settings.database_url, settings.vector_table)
    return ingest(documents_dir, services.store, indexed, settings)


def ask(
    services: Services,
    question: str,
    k: int = DEFAULT_K,
    version: str | None = None,
    product: str | None = None,
    doc_type: str | None = None,
    sources: Set[str] | None = None,
) -> Answer:
    """Retrieve passages and answer from them; `sources` is empty when the model found no answer, `retrieved` always
    holds every passage the model was given. With `sources` (from tag_sources()), only those documents are searched;
    an empty set means no document has the chosen tags, so the answer is the fixed "I don't know" reply, without
    searching or calling the model."""
    if services.chat_model is None:
        raise ValueError("ask() needs services built with a chat model")
    if sources is not None and not sources:
        return Answer(text=NO_ANSWER, sources=[], retrieved=[])
    docs = find_passages(services, question, k=k, version=version, product=product, doc_type=doc_type,
                         sources=sources)
    result = answer(question, docs, services.chat_model)
    return Answer(text=result.text, sources=sources_to_show(result.text, docs), retrieved=docs)


def find_passages(
    services: Services,
    question: str,
    k: int = DEFAULT_K,
    version: str | None = None,
    product: str | None = None,
    doc_type: str | None = None,
    sources: Set[str] | None = None,
) -> list[Document]:
    """The k passages to answer from, best first: vector search, merged with keyword search when HYBRID_SEARCH is
    on, then the configured document-type preference. `sources` limits the search to those files."""
    settings = services.settings
    prefer = None
    if settings.preferred_doc_type and settings.preferred_doc_type_slots > 0:
        prefer = Preference(settings.preferred_doc_type, settings.preferred_doc_type_slots,
                            settings.preferred_doc_type_max_gap)
    hybrid = None
    if settings.hybrid_search and services.keyword_search is not None:
        hybrid = Hybrid(services.keyword_search, settings.hybrid_rrf_k)
    return retrieve(services.store, question, k=k, version=version, product=product, prefer=prefer, hybrid=hybrid,
                    doc_type=doc_type, sources=sources)


def tag_sources(tags: TagLists, selected: Mapping[str, Sequence[str]] | None) -> set[str] | None:
    """The source files of the documents having the chosen extra tags (every category, any of its values), or None
    when no value is chosen (no filter). Raises the tag lists' TagNotFound for a category that isn't an extra one."""
    chosen = {key: list(codes) for key, codes in (selected or {}).items() if codes}
    if not chosen:
        return None
    return tags.sources_with_tags(chosen)


def sources_to_show(answer_text: str, docs: list[Document]) -> list[Document]:
    """The passages to list under an answer: none when the model said it doesn't know."""
    return [] if NO_ANSWER in answer_text else docs
