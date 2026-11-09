"""Live check: keyword (full-text) search and hybrid retrieval against the local Docker pgvector database and Ollama.

Starts from a table made without the full-text search column and already holding chunks (as before hybrid search
existed), then lets get_vector_store() add the column, as it will on an existing installation.

Needs `docker compose up -d` and Ollama running. Run with: pytest -m integration
"""

import os
from dataclasses import replace

import psycopg
import pytest
from langchain_core.documents import Document

from rag.config import get_settings
from rag.providers import get_embeddings, get_keyword_search, get_vector_store
from rag.retrieval.keyword import TSV_COLUMN, ensure_text_search
from rag.retrieval.search import Hybrid, retrieve

pytestmark = pytest.mark.integration

LOCAL_DB = "postgresql://rag:rag_local_dev@127.0.0.1:5433/rag"
TABLE = "it_keyword_chunks"


def _chunk(text, version="1.0", product="acme-dms", doc_type="release-notes"):
    return Document(page_content=text, metadata={
        "source": f"{product}/{version}/{doc_type}/notes.md", "product": product, "version": version,
        "doc_type": doc_type, "page": 1, "embedding_model": "nomic-embed-text",
    })


CHUNKS = [
    _chunk("Known issues. CS-123456: Search results are empty after an upgrade. Workaround: rebuild the full-text "
           "index from the administration console."),
    _chunk("Supported databases: Oracle 21c and Postgres 13.x up to Postgres 15.x on Linux and Windows."),
    _chunk("The web client now loads folders faster and shows thumbnails for images. The Remote Agent is no longer "
           "installed with the Acme Server; install it separately from the agent package.", doc_type="upgrade-guide"),
    _chunk("Installing the Acme Server: run the installer, choose the database and enter the repository owner."),
    _chunk("Known issues. CS-123456 does not apply to this version.", version="2.0"),
    _chunk("Installing the Acme Server on 2.0 needs 16 GB of RAM.", version="2.0"),
]


def _execute(db_url, query, params=()):
    with psycopg.connect(db_url, autocommit=True) as conn:
        return conn.execute(query, params).fetchall()


@pytest.fixture(scope="module")
def env():
    db_url = os.environ.get("DATABASE_URL", LOCAL_DB)

    def drop():
        with psycopg.connect(db_url, autocommit=True) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {TABLE}")

    drop()
    before = get_settings({"DATABASE_URL": db_url, "VECTOR_TABLE": TABLE, "HYBRID_SEARCH": "false"})
    embeddings = get_embeddings(before)
    get_vector_store(before, embeddings).add_documents(CHUNKS)  # a populated table without the column
    assert not _has_column(db_url)

    settings = replace(before, hybrid_search=True)
    store = get_vector_store(settings, embeddings)  # adds the column to the existing table
    yield {"db_url": db_url, "store": store, "search": get_keyword_search(settings), "embeddings": embeddings}
    drop()


def _has_column(db_url):
    query = "SELECT count(*) FROM information_schema.columns WHERE table_name = %s AND column_name = %s"
    return _execute(db_url, query, (TABLE, TSV_COLUMN))[0][0] == 1


def _search(env, question, filters=None, limit=5):
    embedding = env["embeddings"].embed_query(question)
    return env["search"](question, embedding, filters or {}, limit)


def test_the_column_and_index_are_added_to_an_existing_table_and_filled(env):
    db_url = env["db_url"]
    assert _has_column(db_url)
    filled = _execute(db_url, f"SELECT count(*) FROM {TABLE} WHERE {TSV_COLUMN} <> ''::tsvector")[0][0]
    assert filled == len(CHUNKS)
    assert _execute(db_url, "SELECT to_regclass(%s) IS NOT NULL", (f"{TABLE}_{TSV_COLUMN}_idx",))[0][0]
    ensure_text_search(db_url, TABLE)  # a second time changes nothing and doesn't fail


def test_new_chunks_are_indexed_by_the_database(env):
    env["store"].add_documents([_chunk("Zebra-77 appears only here.", version="9.9")])
    hits = _search(env, "zebra-77", {"version": "9.9"})
    assert [d.page_content for d in hits] == ["Zebra-77 appears only here."]


def test_an_issue_id_is_found_and_filters_are_respected(env):
    hits = _search(env, "Is there a workaround for CS-123456?", {"version": "1.0", "product": "acme-dms"})
    assert "Workaround" in hits[0].page_content
    assert {d.metadata["version"] for d in hits} == {"1.0"}
    other = _search(env, "Is there a workaround for CS-123456?", {"version": "2.0"})
    assert other and {d.metadata["version"] for d in other} == {"2.0"}
    assert _search(env, "CS-123456", {"product": "other-product"}) == []


@pytest.mark.parametrize("question", ["Is Oracle 21c supported?", "Which Postgres 13.x releases work?"])
def test_versions_and_names_are_found(env, question):
    assert "Supported databases" in _search(env, question, {"version": "1.0"})[0].page_content


def test_hits_carry_the_same_cosine_distance_as_the_vector_search(env):
    question = "Is the Remote Agent installed with the server?"
    embedding = env["embeddings"].embed_query(question)
    by_vector = {doc.page_content: distance
                 for doc, distance in env["store"].similarity_search_with_score_by_vector(embedding, k=10)}
    for hit in env["search"](question, embedding, {}, 5):
        assert hit.metadata["distance"] == pytest.approx(by_vector[hit.page_content], abs=1e-6)
        assert hit.id and hit.metadata["source"] and hit.metadata["doc_type"]


def test_questions_of_stop_words_or_query_syntax_are_harmless(env):
    assert _search(env, "What is it and where is it?") == []  # only English stop words
    _search(env, "What's new in 'Acme' (beta) & | ! <-> :* \\ 2.0?")  # no syntax error


def test_hybrid_retrieval_finds_a_fact_inside_a_chunk_about_something_else(env):
    hybrid = Hybrid(env["search"])
    docs = retrieve(env["store"], "Is the Remote Agent installed with the Acme Server?", k=2, version="1.0",
                    hybrid=hybrid)
    assert len(docs) == 2
    agent = next(d for d in docs if "Remote Agent" in d.page_content)
    assert agent.metadata["found_by"] in ("keyword", "both")
    assert 0 <= agent.metadata["distance"] <= 2
