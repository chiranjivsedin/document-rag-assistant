import pytest

from rag.database.connection import async_database_url, plain_database_url


@pytest.mark.parametrize("url, expected", [
    ("postgresql://u:p@127.0.0.1:5433/rag", "postgresql+asyncpg://u:p@127.0.0.1:5433/rag"),
    ("postgres://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
    ("postgresql+asyncpg://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
])
def test_async_database_url(url, expected):
    assert async_database_url(url) == expected


@pytest.mark.parametrize("url, expected", [
    ("postgresql+asyncpg://u:p@h/db", "postgresql://u:p@h/db"),
    ("postgresql://u:p@h/db", "postgresql://u:p@h/db"),
])
def test_plain_database_url(url, expected):
    assert plain_database_url(url) == expected


@pytest.mark.parametrize("convert", [async_database_url, plain_database_url])
def test_rejects_urls_without_scheme(convert):
    with pytest.raises(ValueError, match="DATABASE_URL must look like"):
        convert("localhost:5432")
