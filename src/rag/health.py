"""Pre-flight checks, so the scripts stop with one clear line when a service is down or a model is missing."""

import json
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

import psycopg

from rag.config import Settings
from rag.database import connection


def check_services(settings: Settings, need_chat_model: bool) -> list[str]:
    """Problems that would stop the pipeline, as user-facing messages (empty = all good)."""
    models = []
    if settings.embedding_provider == "ollama":
        models.append(settings.embedding_model)
    if need_chat_model and settings.llm_provider == "ollama":
        models.append(settings.llm_model)

    problems = _ollama_problems(settings.ollama_base_url, models) if models else []
    if settings.vector_store == "pgvector":
        problems += _database_problems(settings.database_url)
    return problems


def _ollama_problems(base_url: str, models: list[str]) -> list[str]:
    try:
        with urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=5) as response:
            installed = {model["name"] for model in json.load(response)["models"]}
    except (URLError, OSError, ValueError, KeyError):
        return [f"Can't reach Ollama at {base_url}. Is it running? Start Ollama, or check OLLAMA_BASE_URL."]
    return [f"Ollama model '{m}' is not installed. Run: ollama pull {m}" for m in models if _tagged(m) not in installed]


def _tagged(model: str) -> str:
    """Ollama lists "nomic-embed-text" as "nomic-embed-text:latest"."""
    return model if ":" in model else f"{model}:latest"


def _database_problems(database_url: str | None) -> list[str]:
    if not database_url:
        return ["DATABASE_URL is not set. Copy .env.example to .env (it points at the local Docker database)."]
    try:
        with connection.connect(database_url):
            return []
    except psycopg.OperationalError as error:
        where = urlsplit(connection.plain_database_url(database_url))  # host and port only, never the password
        reason = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
        hint = (
            "Check the user name and password in DATABASE_URL."
            if "password authentication failed" in reason
            else "For the local one: is Docker running, and did you run 'docker compose up -d'?"
        )
        return [f"Can't connect to the database at {where.hostname}:{where.port or 5432} ({reason}). {hint}"]
