"""Live check: the filters chat uses for tags and document types, in the real vector store and keyword search.

Tags reach retrieval as a set of source files: the vector store gets {"source": {"$in": [...]}} next to the equality
filters, the keyword search `c.source = ANY(%(sources)s)`. Uses its own table and fake embeddings (8 dimensions), so
only the Docker database is needed, not Ollama. Run with: pytest -m integration
"""

import os

import psycopg
import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding

from rag.config import get_settings
from rag.providers import get_keyword_search, get_vector_store
from rag.retrieval.search import Hybrid, retrieve

pytestmark = pytest.mark.integration

LOCAL_DB = "postgresql://rag:rag_local_dev@127.0.0.1:5433/rag"
TABLE = "it_tag_filter_chunks"


def _chunk(text, name, version="24.4", doc_type="release-notes"):
    return Document(page_content=text, metadata={
        "source": f"content-management/{version}/{doc_type}/{name}", "product": "content-management",
        "version": version, "doc_type": doc_type, "page": 1, "embedding_model": "fake"})


CHUNKS = [
    _chunk("Workflow: the approval step can now be skipped.", "a.md"),
    _chunk("Workflow: tasks can be delegated to a group.", "b.md"),
    _chunk("Workflow: the upgrade keeps running workflows.", "c.md", doc_type="upgrade-guide"),
    _chunk("Workflow: reminders are sent by email.", "d.md", version="24.2"),
]


@pytest.fixture(scope="module")
def env():
    db_url = os.environ.get("DATABASE_URL", LOCAL_DB)

    def drop():
        with psycopg.connect(db_url, autocommit=True) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {TABLE}")

    drop()
    settings = get_settings({"DATABASE_URL": db_url, "VECTOR_TABLE": TABLE, "HYBRID_SEARCH": "true"})
    store = get_vector_store(settings, DeterministicFakeEmbedding(size=8))
    store.add_documents(CHUNKS)
    yield {"store": store, "keyword": get_keyword_search(settings)}
    drop()


def names(docs):
    return sorted(doc.metadata["source"].rsplit("/", 1)[-1] for doc in docs)


def test_the_vector_store_takes_a_sources_filter_with_equality_filters(env):
    a, b, c = (chunk.metadata["source"] for chunk in CHUNKS[:3])
    assert names(retrieve(env["store"], "workflow", k=10, sources={a, c})) == ["a.md", "c.md"]
    assert names(retrieve(env["store"], "workflow", k=10, sources={a, c}, doc_type="release-notes")) == ["a.md"]
    assert names(retrieve(env["store"], "workflow", k=10, sources={b}, version="24.2")) == []
    assert retrieve(env["store"], "workflow", k=10, sources=set()) == []


def test_the_doc_type_filter(env):
    assert names(retrieve(env["store"], "workflow", k=10, doc_type="upgrade-guide")) == ["c.md"]


def test_the_keyword_search_takes_a_sources_filter(env):
    a, d = CHUNKS[0].metadata["source"], CHUNKS[3].metadata["source"]
    embedding = env["store"].embeddings.embed_query("workflow")
    assert names(env["keyword"]("workflow", embedding, {}, 10)) == ["a.md", "b.md", "c.md", "d.md"]
    assert names(env["keyword"]("workflow", embedding, {}, 10, sources={a, d})) == ["a.md", "d.md"]
    assert names(env["keyword"]("workflow", embedding, {"version": "24.4"}, 10, sources={a, d})) == ["a.md"]
    assert env["keyword"]("workflow", embedding, {}, 10, sources=set()) == []


def test_hybrid_retrieval_keeps_to_the_sources(env):
    b = CHUNKS[1].metadata["source"]
    docs = retrieve(env["store"], "workflow tasks", k=3, sources={b}, hybrid=Hybrid(env["keyword"]))
    assert names(docs) == ["b.md"] and docs[0].metadata["found_by"] == "both"
