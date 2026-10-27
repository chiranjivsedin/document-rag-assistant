"""Application settings, loaded from the environment / .env file."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv

SUPPORTED_PROVIDERS = {"ollama"}


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_model: str
    embedding_provider: str
    embedding_model: str
    ollama_base_url: str
    chunk_size: int
    chunk_overlap: int
    documents_dir: str
    database_url: str | None


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
        ollama_base_url=env.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        chunk_size=_int(env, "CHUNK_SIZE", 1000),
        chunk_overlap=_int(env, "CHUNK_OVERLAP", 150),
        documents_dir=env.get("DOCUMENTS_DIR", "documents"),
        database_url=env.get("DATABASE_URL") or None,
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


def _validate(s: Settings) -> None:
    for name, provider in (("LLM_PROVIDER", s.llm_provider), ("EMBEDDING_PROVIDER", s.embedding_provider)):
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"{name} {provider!r} is not supported; use one of {sorted(SUPPORTED_PROVIDERS)}")
    if s.chunk_size <= 0:
        raise ValueError("CHUNK_SIZE must be greater than 0")
    if not 0 <= s.chunk_overlap < s.chunk_size:
        raise ValueError("CHUNK_OVERLAP must be at least 0 and smaller than CHUNK_SIZE")
