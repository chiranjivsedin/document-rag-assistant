"""Keyword search over the chunks table (PostgreSQL full-text search), the second half of hybrid retrieval.

Vector search misses facts stated in one sentence of a chunk about something else, and exact terms (issue IDs
such as CS-123456, product names, versions). This finds chunks sharing the question's words, ranked by how rare
those words are in the searched chunks (an IDF-weighted count of matched words, like BM25 without its term-
frequency and length parts): a word in almost every chunk ("documentum", "server") adds next to nothing, a rare
one ("DFS", an issue ID) a lot. Any one of the question's words is enough to match (OR), because a question in
natural language rarely has all its words in one chunk. Each hit also carries its cosine distance to the question,
computed in the same query, so hits are comparable with the vector search's.

The text is indexed in a generated column (`TSV_COLUMN`), filled by PostgreSQL itself on every insert and update,
so ingestion doesn't change and existing chunks need no re-ingest.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.documents import Document
from psycopg import errors, sql
from psycopg.rows import dict_row

from rag.database import connect

# 'english' drops stop words ("is", "with", "the") and stems ("installed" / "installation" -> "instal"), which
# matters for questions in plain language. Numbers, versions and number-word mixes ("23.4", "21c", the "139907" of
# "CS-139907") go through its 'simple' dictionary unchanged. The column's generated expression and the query must
# use the same configuration: changing it means dropping TSV_COLUMN (and its index) so they are made again.
TEXT_SEARCH_CONFIG = "english"
TSV_COLUMN = "content_tsv"

# The columns PGVectorStore (langchain_postgres v2) gives the table by default.
ID_COLUMN = "langchain_id"
CONTENT_COLUMN = "content"
EMBEDDING_COLUMN = "embedding"
METADATA_JSON_COLUMN = "langchain_metadata"

# Words of the question, as the lexemes the index holds. Each lexeme becomes its own one-word query: re-reading a
# lexeme with the 'simple' configuration gives back the same lexeme (or, for a compound such as "cs-139907", the
# same parts in a row), and plainto_tsquery ignores query syntax, so no character of the question is an operator.
# idf: ln(1 + (N - df + 0.5) / (df + 0.5)), BM25's always-positive form; N = chunks searched, df = those with the word.
_SEARCH = """
WITH terms AS (
    SELECT DISTINCT lexeme
    FROM unnest(tsvector_to_array(to_tsvector({config}, %(question)s))) AS lexeme
),
total AS (
    SELECT count(*) AS n FROM {table} AS c WHERE {scope}
),
weights AS (
    SELECT terms.lexeme,
           ln(1 + (total.n - count(c.{id}) + 0.5) / (count(c.{id}) + 0.5))::float8 AS idf
    FROM terms CROSS JOIN total
    LEFT JOIN {table} AS c ON c.{tsv} @@ plainto_tsquery('simple', terms.lexeme) AND {scope}
    GROUP BY terms.lexeme, total.n
),
scores AS (
    SELECT c.{id} AS id, sum(weights.idf) AS score
    FROM weights
    JOIN {table} AS c ON c.{tsv} @@ plainto_tsquery('simple', weights.lexeme) AND {scope}
    GROUP BY c.{id}
)
SELECT {columns}, (c.{embedding} <=> %(embedding)s::vector)::float8 AS distance, scores.score::float8 AS score
FROM scores JOIN {table} AS c ON c.{id} = scores.id
ORDER BY scores.score DESC, distance
LIMIT %(limit)s
"""


def search_query(table: str, metadata_columns: Sequence[str], filters: Mapping[str, str]) -> sql.Composed:
    """The keyword search statement. Only identifiers we choose go into the SQL text; the question, the
    embedding, the filter values and the limit are parameters."""
    unknown = set(filters) - set(metadata_columns)
    if unknown:
        raise ValueError(f"Can't filter on {sorted(unknown)}: not a metadata column")
    scope = sql.SQL(" AND ").join(
        [sql.SQL("TRUE")]
        + [sql.SQL("c.{} = {}").format(sql.Identifier(name), sql.Placeholder(f"filter_{name}")) for name in filters]
    )
    selected = [ID_COLUMN, CONTENT_COLUMN, METADATA_JSON_COLUMN, *metadata_columns]
    return sql.SQL(_SEARCH).format(
        config=sql.SQL("{}::regconfig").format(sql.Literal(TEXT_SEARCH_CONFIG)),
        table=sql.Identifier(table),
        scope=scope,
        id=sql.Identifier(ID_COLUMN),
        tsv=sql.Identifier(TSV_COLUMN),
        embedding=sql.Identifier(EMBEDDING_COLUMN),
        columns=sql.SQL(", ").join(sql.SQL("c.{}").format(sql.Identifier(name)) for name in selected),
    )


def search_params(question: str, embedding: Sequence[float], filters: Mapping[str, str], limit: int) -> dict[str, Any]:
    return {
        "question": question,
        "embedding": "[" + ",".join(repr(float(value)) for value in embedding) + "]",  # pgvector's text form
        "limit": limit,
        **{f"filter_{name}": value for name, value in filters.items()},
    }


def keyword_search(
    database_url: str,
    table: str,
    metadata_columns: Sequence[str],
    question: str,
    embedding: Sequence[float],
    filters: Mapping[str, str],
    limit: int,
) -> list[Document]:
    """Up to `limit` chunks sharing words with the question, best keyword score first, within `filters` (column ->
    value). Each Document is shaped like the vector store's: its id, metadata from the JSON and metadata columns,
    plus metadata["distance"] (cosine distance to `embedding`)."""
    if limit <= 0 or not question.strip():
        return []
    query = search_query(table, metadata_columns, filters)
    with connect(database_url) as conn, conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(query, search_params(question, embedding, filters, limit)).fetchall()
    return [to_document(row, metadata_columns) for row in rows]


def to_document(row: Mapping[str, Any], metadata_columns: Sequence[str]) -> Document:
    """A result row as the Document PGVectorStore would return for it, with its distance."""
    metadata = dict(row.get(METADATA_JSON_COLUMN) or {})
    metadata.update({name: row[name] for name in metadata_columns})
    metadata["distance"] = row["distance"]
    return Document(id=str(row[ID_COLUMN]), page_content=row[CONTENT_COLUMN], metadata=metadata)


class TextSearchSetupError(RuntimeError):
    """The full-text search column or index couldn't be added (e.g. the database user doesn't own the table)."""


def ensure_text_search(database_url: str, table: str) -> None:
    """Give the chunks table its full-text search column and GIN index, if it lacks them. Idempotent, and fine on a
    table that already holds chunks: PostgreSQL computes the generated column for every existing row while adding
    it (a short exclusive lock; well under a second for a few thousand chunks). Needs the table's owner, not a
    superuser. Checks first, so the usual call (both exist) takes no lock."""
    index = f"{table}_{TSV_COLUMN}_idx"
    add_column = sql.SQL(
        "ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {tsv} tsvector"
        " GENERATED ALWAYS AS (to_tsvector({config}, {content})) STORED"
    ).format(
        table=sql.Identifier(table),
        tsv=sql.Identifier(TSV_COLUMN),
        config=sql.SQL("{}::regconfig").format(sql.Literal(TEXT_SEARCH_CONFIG)),
        content=sql.Identifier(CONTENT_COLUMN),
    )
    add_index = sql.SQL("CREATE INDEX IF NOT EXISTS {index} ON {table} USING gin ({tsv})").format(
        index=sql.Identifier(index), table=sql.Identifier(table), tsv=sql.Identifier(TSV_COLUMN)
    )
    try:
        with connect(database_url) as conn:
            has_column = conn.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = to_regclass(%s) AND attname = %s"
                " AND NOT attisdropped)",
                (table, TSV_COLUMN),
            ).fetchone()[0]
            if not has_column:
                conn.execute(add_column)
            if conn.execute("SELECT to_regclass(%s) IS NULL", (index,)).fetchone()[0]:
                conn.execute(add_index)
    except errors.InsufficientPrivilege as error:
        raise TextSearchSetupError(
            f"Can't add keyword search to table {table!r}: the database user doesn't own it ({error}). Run the "
            f"application once as the table's owner, or set HYBRID_SEARCH=false."
        ) from error
