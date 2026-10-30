import pytest

from rag.config import get_settings


def test_defaults():
    s = get_settings({})
    assert s.llm_provider == "ollama"
    assert s.llm_model == "gemma3:4b"
    assert s.embedding_model == "nomic-embed-text"
    assert s.ollama_base_url == "http://127.0.0.1:11434"
    assert (s.chunk_size, s.chunk_overlap) == (1000, 150)
    assert s.documents_dir == "documents"
    assert s.database_url is None
    assert (s.vector_store, s.vector_table) == ("pgvector", "chunks")
    assert s.api_cors_origins == ("http://localhost:5173",)


def test_cors_origins_are_a_comma_separated_list():
    s = get_settings({"API_CORS_ORIGINS": " http://localhost:5173, https://dms.example.com ,"})
    assert s.api_cors_origins == ("http://localhost:5173", "https://dms.example.com")


def test_overrides():
    s = get_settings({
        "LLM_MODEL": "llama3.2:3b",
        "CHUNK_SIZE": "500",
        "CHUNK_OVERLAP": "50",
        "DATABASE_URL": "postgresql://u:p@localhost:5432/rag",
    })
    assert s.llm_model == "llama3.2:3b"
    assert (s.chunk_size, s.chunk_overlap) == (500, 50)
    assert s.database_url == "postgresql://u:p@localhost:5432/rag"


def test_empty_values_fall_back_to_defaults():
    s = get_settings({"CHUNK_SIZE": "", "DATABASE_URL": ""})
    assert s.chunk_size == 1000
    assert s.database_url is None


def test_non_numeric_chunk_size():
    with pytest.raises(ValueError, match="CHUNK_SIZE must be a whole number"):
        get_settings({"CHUNK_SIZE": "big"})


def test_unsupported_provider():
    with pytest.raises(ValueError, match="LLM_PROVIDER 'azure' is not supported"):
        get_settings({"LLM_PROVIDER": "azure"})


@pytest.mark.parametrize("size, overlap", [("100", "100"), ("100", "-1"), ("0", "0")])
def test_invalid_chunking(size, overlap):
    with pytest.raises(ValueError, match="CHUNK_"):
        get_settings({"CHUNK_SIZE": size, "CHUNK_OVERLAP": overlap})


def test_keep_alive_defaults_to_30_minutes():
    assert get_settings({}).ollama_keep_alive_minutes == 30
    assert get_settings({"OLLAMA_KEEP_ALIVE_MINUTES": "-1"}).ollama_keep_alive_minutes == -1


@pytest.mark.parametrize("value", ["-2", "soon"])
def test_invalid_keep_alive(value):
    with pytest.raises(ValueError, match="OLLAMA_KEEP_ALIVE_MINUTES"):
        get_settings({"OLLAMA_KEEP_ALIVE_MINUTES": value})


def test_unsupported_vector_store():
    with pytest.raises(ValueError, match="VECTOR_STORE 'chroma' is not supported"):
        get_settings({"VECTOR_STORE": "chroma"})


@pytest.mark.parametrize("table", ["chunks; drop table x", "my chunks", "1chunks"])
def test_vector_table_must_be_plain_name(table):
    with pytest.raises(ValueError, match="VECTOR_TABLE must be a plain table name"):
        get_settings({"VECTOR_TABLE": table})


def test_reads_os_environment(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "from-env")
    assert get_settings().llm_model == "from-env"
