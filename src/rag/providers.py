"""The only module that knows which model backends are used.

Everything else asks here for a chat model or embeddings and works with
LangChain's standard interfaces, so switching to Azure later means adding
a branch here, not changing callers.
"""

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.vectorstores import VectorStore

from rag.config import Settings
from rag.database import async_database_url, table_exists

# Chunk metadata kept as real table columns, so retrieval can filter on them in SQL.
METADATA_COLUMNS = {
    "source": "TEXT",
    "file_hash": "TEXT",
    "product": "TEXT",
    "version": "TEXT",
    "doc_type": "TEXT",
    "page": "INTEGER",
    "embedding_model": "TEXT",
}


def get_chat_model(settings: Settings) -> BaseChatModel:
    if settings.llm_provider == "ollama":
        from langchain_ollama import ChatOllama

        # temperature 0: answers about documentation should be consistent, not creative
        return ChatOllama(model=settings.llm_model, base_url=settings.ollama_base_url, temperature=0)
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider!r}")


def get_embeddings(settings: Settings) -> Embeddings:
    if settings.embedding_provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(model=settings.embedding_model, base_url=settings.ollama_base_url)
    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider!r}")


def get_vector_store(settings: Settings, embeddings: Embeddings) -> VectorStore:
    """Connect to the vector store, creating its table on first use."""
    if settings.vector_store == "pgvector":
        return _pgvector_store(settings, embeddings)
    raise ValueError(f"Unsupported vector store: {settings.vector_store!r}")


def _pgvector_store(settings: Settings, embeddings: Embeddings) -> VectorStore:
    from langchain_postgres import Column, PGEngine, PGVectorStore

    if not settings.database_url:
        raise ValueError("DATABASE_URL is required when VECTOR_STORE is pgvector")

    engine = PGEngine.from_connection_string(url=async_database_url(settings.database_url))
    if not table_exists(settings.database_url, settings.vector_table):
        engine.init_vectorstore_table(
            table_name=settings.vector_table,
            vector_size=len(embeddings.embed_query("dimension probe")),
            metadata_columns=[Column(name, sql_type) for name, sql_type in METADATA_COLUMNS.items()],
        )
    return PGVectorStore.create_sync(
        engine=engine,
        embedding_service=embeddings,
        table_name=settings.vector_table,
        metadata_columns=list(METADATA_COLUMNS),
    )
