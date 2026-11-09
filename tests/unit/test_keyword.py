import uuid

import pytest
from psycopg import errors

from rag.retrieval import keyword
from rag.retrieval.keyword import (TextSearchSetupError, ensure_text_search, keyword_search, search_params,
                                   search_query, to_document)

COLUMNS = ["source", "product", "version", "doc_type", "page"]


def sql_text(query) -> str:
    return query.as_string(None)


def test_query_uses_the_generated_column_the_english_config_and_parameters_only():
    text = sql_text(search_query("chunks", COLUMNS, {}))
    assert 'FROM "chunks" AS c' in text
    assert "to_tsvector('english'::regconfig, %(question)s)" in text
    assert 'c."content_tsv" @@ plainto_tsquery(\'simple\', terms.lexeme)' in text
    assert '(c."embedding" <=> %(embedding)s::vector)::float8 AS distance' in text
    assert "LIMIT %(limit)s" in text
    assert 'c."langchain_id", c."content", c."langchain_metadata", c."source", c."product"' in text


def test_filters_become_placeholders_in_every_part_of_the_query():
    text = sql_text(search_query("chunks", COLUMNS, {"version": "23.4", "product": "documentum"}))
    scope = 'TRUE AND c."version" = %(filter_version)s AND c."product" = %(filter_product)s'
    assert text.count(scope) == 3  # the chunk count, the word weights and the scores all search the same chunks
    assert "23.4" not in text and "documentum" not in text


def test_no_filters_searches_every_chunk():
    assert "WHERE TRUE\n" in sql_text(search_query("chunks", COLUMNS, {}))


def test_unknown_filter_columns_are_refused():
    with pytest.raises(ValueError, match="not a metadata column"):
        search_query("chunks", COLUMNS, {"content; DROP TABLE chunks": "x"})


def test_question_and_values_are_sent_as_parameters():
    params = search_params("Is DFS installed? ' OR 1=1 --", [0.25, -1, 3e-05], {"version": "23.4"}, 20)
    assert params == {"question": "Is DFS installed? ' OR 1=1 --", "embedding": "[0.25,-1.0,3e-05]", "limit": 20,
                      "filter_version": "23.4"}


def test_a_row_becomes_a_document_like_the_vector_stores():
    chunk_id = uuid.uuid4()
    row = {"langchain_id": chunk_id, "content": "Section: 2 Notes\nDFS is no longer installed.",
           "langchain_metadata": {"chunk": 3, "page": 0}, "source": "acme/1.0/release-notes/n.pdf", "product": "acme",
           "version": "1.0", "doc_type": "release-notes", "page": 7, "distance": 0.396, "score": 9.5}
    doc = to_document(row, COLUMNS)
    assert doc.id == str(chunk_id)
    assert doc.page_content == row["content"]
    assert doc.metadata == {"chunk": 3, "source": "acme/1.0/release-notes/n.pdf", "product": "acme", "version": "1.0",
                            "doc_type": "release-notes", "page": 7, "distance": 0.396}  # columns win over the JSON


def test_a_row_without_json_metadata():
    row = {"langchain_id": "x", "content": "c", "langchain_metadata": None, **dict.fromkeys(COLUMNS), "distance": 0.1}
    assert to_document(row, COLUMNS).metadata["distance"] == 0.1


class FakeResult:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)

    def fetchall(self):
        return self.value


class FakeConnection:
    """Records statements. `answers` are returned in turn for the checks ensure_text_search makes."""

    def __init__(self, answers=(), rows=(), error=None):
        self.answers = list(answers)
        self.rows = list(rows)
        self.error = error
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        text = query if isinstance(query, str) else sql_text(query)
        self.statements.append((text, params))
        if self.error is not None and text.startswith("ALTER"):
            raise self.error
        return FakeResult(self.answers.pop(0) if text.startswith("SELECT") and self.answers else self.rows)

    def cursor(self, row_factory=None):
        return self


def use_connection(monkeypatch, conn):
    urls = []

    def connect(url):
        urls.append(url)
        return conn

    monkeypatch.setattr(keyword, "connect", connect)
    return urls


def test_keyword_search_runs_the_query_and_returns_documents(monkeypatch):
    row = {"langchain_id": "id-1", "content": "CS-123456 is fixed.", "langchain_metadata": {},
           **dict.fromkeys(COLUMNS, "v"), "distance": 0.3, "score": 4.2}
    conn = FakeConnection(rows=[row])
    urls = use_connection(monkeypatch, conn)
    docs = keyword_search("postgresql://db", "chunks", COLUMNS, "Workaround for CS-123456?", [0.1], {"version": "1"}, 8)
    assert urls == ["postgresql://db"]
    [(text, params)] = conn.statements
    assert "WITH terms AS" in text
    assert params == {"question": "Workaround for CS-123456?", "embedding": "[0.1]", "limit": 8, "filter_version": "1"}
    assert [(d.id, d.metadata["distance"]) for d in docs] == [("id-1", 0.3)]


@pytest.mark.parametrize(("question", "limit"), [("   ", 5), ("q", 0)])
def test_keyword_search_skips_the_database_when_there_is_nothing_to_search(monkeypatch, question, limit):
    urls = use_connection(monkeypatch, FakeConnection())
    assert keyword_search("postgresql://db", "chunks", COLUMNS, question, [0.1], {}, limit) == []
    assert urls == []


def test_ensure_adds_the_generated_column_and_index_to_a_table_without_them(monkeypatch):
    conn = FakeConnection(answers=[False, True])  # no column yet; index missing
    use_connection(monkeypatch, conn)
    ensure_text_search("postgresql://db", "chunks")
    statements = [text for text, _ in conn.statements]
    assert statements[1] == ('ALTER TABLE "chunks" ADD COLUMN IF NOT EXISTS "content_tsv" tsvector GENERATED ALWAYS '
                             "AS (to_tsvector('english'::regconfig, \"content\")) STORED")
    assert statements[3] == 'CREATE INDEX IF NOT EXISTS "chunks_content_tsv_idx" ON "chunks" USING gin ("content_tsv")'
    assert conn.statements[0][1] == ("chunks", "content_tsv")
    assert conn.statements[2][1] == ("chunks_content_tsv_idx",)


def test_ensure_changes_nothing_when_both_exist(monkeypatch):
    conn = FakeConnection(answers=[True, False])  # column there; index not missing
    use_connection(monkeypatch, conn)
    ensure_text_search("postgresql://db", "chunks")
    assert all(text.startswith("SELECT") for text, _ in conn.statements)


def test_ensure_explains_a_missing_ownership(monkeypatch):
    use_connection(monkeypatch, FakeConnection(answers=[False], error=errors.InsufficientPrivilege("must be owner")))
    with pytest.raises(TextSearchSetupError, match="doesn't own it.*HYBRID_SEARCH=false"):
        ensure_text_search("postgresql://db", "chunks")
