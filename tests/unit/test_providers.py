from dataclasses import replace

import pytest
from langchain_ollama import ChatOllama, OllamaEmbeddings

from rag.config import get_settings
from rag.providers import get_chat_model, get_embeddings, get_vector_store


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
    assert embeddings.base_url == "http://127.0.0.1:11434"


def test_models_stay_loaded_between_questions():
    # Ollama's default unloads a model after 5 idle minutes; reloading gemma3:4b added 15-29 s to the next answer.
    settings = get_settings({"OLLAMA_KEEP_ALIVE_MINUTES": "45"})
    assert get_chat_model(settings).keep_alive == 45 * 60
    assert get_embeddings(settings).keep_alive == 45 * 60


def test_keep_alive_minus_one_keeps_models_loaded_until_ollama_stops():
    settings = get_settings({"OLLAMA_KEEP_ALIVE_MINUTES": "-1"})
    assert get_chat_model(settings).keep_alive == -1
    assert get_embeddings(settings).keep_alive == -1


def test_unknown_providers_are_rejected():
    settings = get_settings({})
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        get_chat_model(replace(settings, llm_provider="other"))
    with pytest.raises(ValueError, match="Unsupported embedding provider"):
        get_embeddings(replace(settings, embedding_provider="other"))
    with pytest.raises(ValueError, match="Unsupported vector store"):
        get_vector_store(replace(settings, vector_store="other"), embeddings=None)


def test_pgvector_requires_database_url():
    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        get_vector_store(get_settings({}), embeddings=None)


def test_error_groups_cover_the_backends_failures():
    import httpx
    import ollama
    import psycopg
    import sqlalchemy.exc

    from rag.providers import model_errors, unavailable_errors

    unavailable = unavailable_errors()
    # the Ollama client raises the built-in ConnectionError when it can't connect
    assert isinstance(ConnectionError("Failed to connect to Ollama"), unavailable)
    assert isinstance(psycopg.OperationalError("down"), unavailable)
    assert isinstance(sqlalchemy.exc.OperationalError("SELECT 1", {}, Exception("down")), unavailable)
    assert isinstance(ollama.ResponseError("out of memory", 500), model_errors())
    assert isinstance(httpx.ReadTimeout("slow"), model_errors())
    assert not isinstance(ollama.ResponseError("x"), unavailable)
