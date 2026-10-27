"""PostgreSQL connection handling (psycopg), plus the URL form the async vector store needs."""

import psycopg


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
