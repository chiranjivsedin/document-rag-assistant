"""How backend failures (Ollama or the database unreachable, a failed model call) become HTTP errors."""

from rag.providers import model_errors, unavailable_errors

UNAVAILABLE = "Ollama or the database stopped responding. Check GET /health."


def backend_failure(error: BaseException) -> tuple[int, str] | None:
    """HTTP status and client-safe message for a backend failure, or None if it isn't one."""
    if isinstance(error, unavailable_errors()):
        return 503, UNAVAILABLE
    if isinstance(error, model_errors()):
        reason = getattr(error, "error", None) or type(error).__name__  # Ollama's own reason when it gives one
        return 502, f"The model service failed: {str(reason)[:300]}"
    return None
