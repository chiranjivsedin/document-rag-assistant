"""Prompt templates for answer generation."""

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate

NO_ANSWER = "I don't know based on the available documents."

ANSWER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You answer questions about product documentation using only the numbered context passages. "
        "After each statement, cite the passage(s) it comes from, like [1] or [2][3]. "
        "If the passages do not contain the answer, reply exactly: " + NO_ANSWER + " "
        "Do not use outside knowledge. Be concise.",
    ),
    ("human", "Context passages:\n\n{context}\n\nQuestion: {question}"),
])


def format_context(docs: list[Document]) -> str:
    """Number each passage and label it with where it came from."""
    return "\n\n".join(f"[{number}] ({source_label(doc)})\n{doc.page_content}" for number, doc in enumerate(docs, 1))


def source_label(doc: Document) -> str:
    meta = doc.metadata
    parts = [meta.get("source", "unknown source")]
    if meta.get("version"):
        parts.append(f"version {meta['version']}")
    if meta.get("page"):
        parts.append(f"page {meta['page']}")
    return ", ".join(parts)
