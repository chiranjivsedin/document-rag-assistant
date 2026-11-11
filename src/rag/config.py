"""Application settings, loaded from the environment / .env file."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv

SUPPORTED_PROVIDERS = {"ollama"}
SUPPORTED_VECTOR_STORES = {"pgvector"}


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_model: str
    embedding_provider: str
    embedding_model: str
    ollama_base_url: str
    ollama_keep_alive_minutes: int  # how long Ollama keeps a model loaded after a call; -1 = until Ollama stops
    chunk_size: int
    chunk_overlap: int
    # Retrieval keeps up to preferred_doc_type_slots of the passages for this document type (None = off), if
    # within preferred_doc_type_max_gap of the closest passage's distance (see retrieval.search.Preference).
    preferred_doc_type: str | None
    preferred_doc_type_slots: int
    preferred_doc_type_max_gap: float
    # Hybrid retrieval: also search the chunks by keyword (PostgreSQL full-text search) and merge both rankings
    # with Reciprocal Rank Fusion, a passage scoring 1 / (hybrid_rrf_k + its rank) per list (retrieval.search.fuse).
    hybrid_search: bool
    hybrid_rrf_k: int
    documents_dir: str
    max_upload_mb: int  # largest file the document library accepts
    database_url: str | None
    vector_store: str
    vector_table: str
    api_cors_origins: tuple[str, ...]  # browser origins allowed to call the HTTP API
    frontend_dist: str | None  # built web app (frontend/dist) the API serves at /; None = API only
    question_log: str | None  # file the API appends one JSON line per /chat question to (rag.question_log); None = off


def get_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build settings from `env`, or from os.environ after loading .env."""
    if env is None:
        load_dotenv()
        env = os.environ

    settings = Settings(
        llm_provider=env.get("LLM_PROVIDER", "ollama"),
        llm_model=env.get("LLM_MODEL", "gemma3:4b"),
        embedding_provider=env.get("EMBEDDING_PROVIDER", "ollama"),
        embedding_model=env.get("EMBEDDING_MODEL", "nomic-embed-text"),
        ollama_base_url=env.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        ollama_keep_alive_minutes=_int(env, "OLLAMA_KEEP_ALIVE_MINUTES", 30),
        chunk_size=_int(env, "CHUNK_SIZE", 1000),
        chunk_overlap=_int(env, "CHUNK_OVERLAP", 150),
        preferred_doc_type=env.get("PREFERRED_DOC_TYPE", "release-notes").strip() or None,
        preferred_doc_type_slots=_int(env, "PREFERRED_DOC_TYPE_SLOTS", 2),
        preferred_doc_type_max_gap=_float(env, "PREFERRED_DOC_TYPE_MAX_GAP", 0.15),
        hybrid_search=_bool(env, "HYBRID_SEARCH", True),
        hybrid_rrf_k=_int(env, "HYBRID_RRF_K", 10),
        documents_dir=env.get("DOCUMENTS_DIR", "documents"),
        max_upload_mb=_int(env, "MAX_UPLOAD_MB", 50),
        database_url=env.get("DATABASE_URL") or None,
        vector_store=env.get("VECTOR_STORE", "pgvector"),
        vector_table=env.get("VECTOR_TABLE", "chunks"),
        api_cors_origins=_list(env.get("API_CORS_ORIGINS") or "http://localhost:5173"),
        frontend_dist=(env.get("FRONTEND_DIST") or "").strip() or None,
        question_log=(env.get("QUESTION_LOG") or "").strip() or None,
    )
    _validate(settings)
    return settings


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, got {raw!r}") from None


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}") from None


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = (env.get(name) or "").strip().lower()
    if raw == "":
        return default
    if raw in ("true", "1", "yes", "on"):
        return True
    if raw in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"{name} must be true or false, got {env.get(name)!r}")


def _list(raw: str) -> tuple[str, ...]:
    """Comma-separated values, trimmed, empty items dropped."""
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _validate(s: Settings) -> None:
    for name, provider in (("LLM_PROVIDER", s.llm_provider), ("EMBEDDING_PROVIDER", s.embedding_provider)):
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"{name} {provider!r} is not supported; use one of {sorted(SUPPORTED_PROVIDERS)}")
    if s.vector_store not in SUPPORTED_VECTOR_STORES:
        raise ValueError(
            f"VECTOR_STORE {s.vector_store!r} is not supported; use one of {sorted(SUPPORTED_VECTOR_STORES)}"
        )
    if not s.vector_table.isidentifier():
        raise ValueError(f"VECTOR_TABLE must be a plain table name, got {s.vector_table!r}")
    if s.ollama_keep_alive_minutes < -1:
        raise ValueError("OLLAMA_KEEP_ALIVE_MINUTES must be -1 (keep loaded) or a number of minutes from 0")
    if s.chunk_size <= 0:
        raise ValueError("CHUNK_SIZE must be greater than 0")
    if not 0 <= s.chunk_overlap < s.chunk_size:
        raise ValueError("CHUNK_OVERLAP must be at least 0 and smaller than CHUNK_SIZE")
    if s.preferred_doc_type_slots < 0:
        raise ValueError("PREFERRED_DOC_TYPE_SLOTS must be 0 (off) or more")
    if not 0 <= s.preferred_doc_type_max_gap <= 2:
        raise ValueError("PREFERRED_DOC_TYPE_MAX_GAP must be a cosine distance from 0 to 2")
    if s.hybrid_rrf_k < 0:
        raise ValueError("HYBRID_RRF_K must be 0 or more")
    if s.max_upload_mb <= 0:
        raise ValueError("MAX_UPLOAD_MB must be greater than 0")
