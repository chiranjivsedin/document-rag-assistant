"""Live check: retrieval with version filters against the local Docker pgvector database and Ollama.

Needs `docker compose up -d` and Ollama running. Run with: pytest -m integration
"""

import os

import psycopg
import pytest
from langchain_core.documents import Document

from rag.config import get_settings
from rag.providers import get_embeddings, get_vector_store
from rag.retrieval.search import retrieve

pytestmark = pytest.mark.integration

LOCAL_DB = "postgresql://rag:rag_local_dev@127.0.0.1:5433/rag"
TABLE = "it_retrieval_chunks"


@pytest.fixture(scope="module")
def store():
    db_url = os.environ.get("DATABASE_URL", LOCAL_DB)

    def drop():
        with psycopg.connect(db_url, autocommit=True) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {TABLE}")

    drop()
    settings = get_settings({"DATABASE_URL": db_url, "VECTOR_TABLE": TABLE})
    store = get_vector_store(settings, get_embeddings(settings))
    store.add_documents([
        _chunk("The Classic Desktop client is deprecated and will be removed in 2.0.", "acme-dms", "1.0"),
        _chunk("The Classic Desktop client has been removed.", "acme-dms", "2.0"),
        _chunk("The server requires 8 GB of RAM.", "acme-dms", "1.0"),
        _chunk("The Classic Desktop client is fully supported.", "other-product", "1.0"),
    ])
    yield store
    drop()


def _chunk(text, product, version):
    return Document(page_content=text, metadata={"source": f"{product}/{version}/notes.md", "product": product,
                                                 "version": version, "embedding_model": "nomic-embed-text"})


def test_closest_chunk_comes_first(store):
    docs = retrieve(store, "How much memory does the server need?", k=4)
    assert "RAM" in docs[0].page_content
    assert docs[0].metadata["distance"] <= docs[-1].metadata["distance"]


def test_version_filter(store):
    docs = retrieve(store, "Is the Classic Desktop client deprecated?", version="2.0")
    assert [d.metadata["version"] for d in docs] == ["2.0"]


def test_version_and_product_filter(store):
    docs = retrieve(store, "Is the Classic Desktop client deprecated?", version="1.0", product="acme-dms")
    assert {(d.metadata["product"], d.metadata["version"]) for d in docs} == {("acme-dms", "1.0")}
    assert len(docs) == 2


def test_unknown_version_returns_nothing(store):
    assert retrieve(store, "anything", version="9.9") == []
