"""Read/write queries for documents and chunks."""

from psycopg import sql

from rag.database.connection import connect


def table_exists(database_url: str, table: str) -> bool:
    with connect(database_url) as conn:
        return conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[0]


def indexed_files(database_url: str, table: str) -> dict[str, tuple[str, str]]:
    """Each indexed source file → (file_hash, embedding_model) of its stored chunks."""
    if not table_exists(database_url, table):
        return {}
    query = sql.SQL("SELECT source, min(file_hash), min(embedding_model) FROM {} GROUP BY source").format(
        sql.Identifier(table)
    )
    with connect(database_url) as conn:
        return {source: (file_hash, model) for source, file_hash, model in conn.execute(query)}
