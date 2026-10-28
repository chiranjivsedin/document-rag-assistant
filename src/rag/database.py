"""PostgreSQL helpers (psycopg): connections, the URL form the async vector store needs, and small queries."""

import psycopg
from psycopg import sql


def connect(database_url: str) -> psycopg.Connection:
    """Open a psycopg connection; fails after 10 s instead of hanging on a bad address."""
    return psycopg.connect(plain_database_url(database_url), connect_timeout=10)


def plain_database_url(url: str) -> str:
    """postgresql+asyncpg://… → postgresql://… (psycopg takes no "+driver" suffix)."""
    scheme, sep, rest = _split(url)
    return f"{scheme.split('+')[0]}{sep}{rest}"


def async_database_url(url: str) -> str:
    """postgresql://… → postgresql+asyncpg://… (PGEngine needs an async driver; asyncpg works on Windows)."""
    scheme, sep, rest = _split(url)
    return f"postgresql+asyncpg{sep}{rest}" if scheme in ("postgresql", "postgres") else url


def _split(url: str) -> tuple[str, str, str]:
    scheme, sep, rest = url.partition("://")
    if not sep:
        raise ValueError("DATABASE_URL must look like postgresql://user:password@host:port/database")
    return scheme, sep, rest


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
