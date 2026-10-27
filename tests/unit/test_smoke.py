"""Smoke test: the package and its key dependencies import."""


def test_imports():
    import langchain_core  # noqa: F401
    import langchain_ollama  # noqa: F401
    import langchain_text_splitters  # noqa: F401
    import pypdf  # noqa: F401
    import rag  # noqa: F401
