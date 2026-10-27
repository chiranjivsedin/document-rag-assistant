from dataclasses import replace

import pytest
from langchain_ollama import ChatOllama, OllamaEmbeddings

from rag.config import get_settings
from rag.providers import get_chat_model, get_embeddings


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
