"""The only module that knows which model backends are used.

Everything else asks here for a chat model or embeddings and works with
LangChain's standard interfaces, so switching to Azure later means adding
a branch here, not changing callers.
"""

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from rag.config import Settings


def get_chat_model(settings: Settings) -> BaseChatModel:
    if settings.llm_provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(model=settings.llm_model, base_url=settings.ollama_base_url)
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider!r}")


def get_embeddings(settings: Settings) -> Embeddings:
    if settings.embedding_provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(model=settings.embedding_model, base_url=settings.ollama_base_url)
    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider!r}")
