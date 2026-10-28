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
from rag.providers import get_chat_model, get_embeddings, get_vector_store
from rag.retrieval.search import retrieve


@dataclass(frozen=True)
class Services:
    settings: Settings
    store: VectorStore
    chat_model: BaseChatModel | None  # None when built only for ingesting


def build_services(settings: Settings, with_chat_model: bool = True) -> Services:
    """Connect to the vector store (creating its table on first use) and set up the chat model."""
    store = get_vector_store(settings, get_embeddings(settings))
    chat_model = get_chat_model(settings) if with_chat_model else None
    return Services(settings=settings, store=store, chat_model=chat_model)


def run_ingest(services: Services, documents_dir: Path) -> IngestReport:
    """Bring the store in line with documents_dir; files already stored unchanged are skipped."""
    settings = services.settings
    indexed = indexed_files(settings.database_url, settings.vector_table)
    return ingest(documents_dir, services.store, indexed, settings)


def ask(
    services: Services,
    question: str,
    k: int = 5,
    version: str | None = None,
    product: str | None = None,
) -> Answer:
    """Retrieve passages and answer from them; `sources` is empty when the model found no answer."""
    if services.chat_model is None:
        raise ValueError("ask() needs services built with a chat model")
    docs = retrieve(services.store, question, k=k, version=version, product=product)
    result = answer(question, docs, services.chat_model)
    return Answer(text=result.text, sources=sources_to_show(result.text, docs))


def sources_to_show(answer_text: str, docs: list[Document]) -> list[Document]:
    """The passages to list under an answer: none when the model said it doesn't know."""
    return [] if NO_ANSWER in answer_text else docs
