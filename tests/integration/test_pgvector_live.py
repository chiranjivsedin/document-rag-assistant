"""Live check: store and search chunks in the local Docker pgvector database with real Ollama embeddings.

Needs `docker compose up -d` and Ollama running. Run with: pytest -m integration
"""

import os

import psycopg
import pytest
from langchain_core.documents import Document

from rag.config import get_settings
from rag.providers import get_embeddings, get_vector_store

pytestmark = pytest.mark.integration

LOCAL_DB = "postgresql://rag:rag_local_dev@127.0.0.1:5433/rag"
TABLE = "it_chunks"


@pytest.fixture
def store():
    db_url = os.environ.get("DATABASE_URL", LOCAL_DB)

    def drop():
        with psycopg.connect(db_url, autocommit=True) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {TABLE}")

    drop()
    settings = get_settings({"DATABASE_URL": db_url, "VECTOR_TABLE": TABLE})
    yield get_vector_store(settings, get_embeddings(settings))
    drop()


def _doc(text, version):
    return Document(page_content=text, metadata={
        "source": f"documentum/{version}/release-notes/notes.pdf", "product": "documentum",
        "version": version, "doc_type": "release-notes", "page": 1, "embedding_model": "nomic-embed-text",
    })


def test_search_and_version_filter(store):
    store.add_documents([
        _doc("The Webtop client is deprecated and will be removed in a future release.", "16.2"),
        _doc("Webtop has been removed; use D2 or Smart View instead.", "23.4"),
        _doc("Installation requires 16 GB of RAM on the Content Server host.", "16.2"),
    ])

    top = store.similarity_search("Is Webtop deprecated?", k=1)
    assert "Webtop" in top[0].page_content

    only_16_2 = store.similarity_search("Is Webtop deprecated?", k=3, filter={"version": "16.2"})
    assert {d.metadata["version"] for d in only_16_2} == {"16.2"}
    assert len(only_16_2) == 2
