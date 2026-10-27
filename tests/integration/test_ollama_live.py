"""Live checks against the local Ollama server (run with: pytest -m integration)."""

import pytest

from rag.config import get_settings
from rag.providers import get_chat_model, get_embeddings

pytestmark = pytest.mark.integration


def test_embedding_has_expected_size():
    vector = get_embeddings(get_settings()).embed_query("Deprecated features in version 16.2")
    assert len(vector) == 768  # nomic-embed-text
    assert all(isinstance(x, float) for x in vector)


def test_chat_model_replies():
    reply = get_chat_model(get_settings()).invoke("Reply with exactly one word: ready")
    assert reply.content.strip()
