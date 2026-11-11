"""Shared steps behind the entry points (the CLI scripts now, the HTTP API next).

Builds the vector store and chat model once, runs an ingest against what the database already
holds, and answers a question, so each entry point doesn't repeat this glue.
"""

from dataclasses import dataclass
from pathlib import Path

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
) -> Answer:
    """Retrieve passages and answer from them; `sources` is empty when the model found no answer, `retrieved` always
    holds every passage the model was given."""
    if services.chat_model is None:
        raise ValueError("ask() needs services built with a chat model")
    docs = find_passages(services, question, k=k, version=version, product=product)
    result = answer(question, docs, services.chat_model)
    return Answer(text=result.text, sources=sources_to_show(result.text, docs), retrieved=docs)


def find_passages(
    services: Services,
    question: str,
    k: int = DEFAULT_K,
    version: str | None = None,
    product: str | None = None,
) -> list[Document]:
    """The k passages to answer from, best first: vector search, merged with keyword search when HYBRID_SEARCH is
    on, then the configured document-type preference."""
    settings = services.settings
    prefer = None
    if settings.preferred_doc_type and settings.preferred_doc_type_slots > 0:
        prefer = Preference(settings.preferred_doc_type, settings.preferred_doc_type_slots,
                            settings.preferred_doc_type_max_gap)
    hybrid = None
    if settings.hybrid_search and services.keyword_search is not None:
        hybrid = Hybrid(services.keyword_search, settings.hybrid_rrf_k)
    return retrieve(services.store, question, k=k, version=version, product=product, prefer=prefer, hybrid=hybrid)


def sources_to_show(answer_text: str, docs: list[Document]) -> list[Document]:
    """The passages to list under an answer: none when the model said it doesn't know."""
    return [] if NO_ANSWER in answer_text else docs
