from dataclasses import replace

import pytest
from langchain_ollama import ChatOllama, OllamaEmbeddings

from rag.config import get_settings
from rag.providers import async_database_url, get_chat_model, get_embeddings, get_vector_store


def test_ollama_chat_model_uses_settings():
    settings = get_settings({"LLM_MODEL": "llama3.2:3b", "OLLAMA_BASE_URL": "http://ollama:11434"})
    model = get_chat_model(settings)
    assert isinstance(model, ChatOllama)
    assert model.model == "llama3.2:3b"
    assert model.base_url == "http://ollama:11434"


def test_ollama_embeddings_use_settings():
    settings = get_settings({"EMBEDDING_MODEL": "mxbai-embed-large"})
    embeddings = get_embeddings(settings)
    assert isinstance(embeddings, OllamaEmbeddings)
    assert embeddings.model == "mxbai-embed-large"
    assert embeddings.base_url == "http://localhost:11434"


def test_unknown_providers_are_rejected():
    settings = get_settings({})
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        get_chat_model(replace(settings, llm_provider="other"))
    with pytest.raises(ValueError, match="Unsupported embedding provider"):
        get_embeddings(replace(settings, embedding_provider="other"))
    with pytest.raises(ValueError, match="Unsupported vector store"):
        get_vector_store(replace(settings, vector_store="other"), embeddings=None)


@pytest.mark.parametrize("url, expected", [
    ("postgresql://u:p@localhost:5433/rag", "postgresql+asyncpg://u:p@localhost:5433/rag"),
    ("postgres://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
    ("postgresql+asyncpg://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
])
def test_async_database_url(url, expected):
    assert async_database_url(url) == expected


def test_async_database_url_rejects_garbage():
    with pytest.raises(ValueError, match="DATABASE_URL must look like"):
        async_database_url("localhost:5432")


def test_pgvector_requires_database_url():
    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        get_vector_store(get_settings({}), embeddings=None)
