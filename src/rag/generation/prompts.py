"""Prompt templates for answer generation."""

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate

NO_ANSWER = "I don't know based on the available documents."

ANSWER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        # Each rule answers a failure seen with gemma3:4b: a bare "[1]" as the whole answer, claims the passages
        # don't make, facts from different versions mixed up, and [1][2] where [2] says nothing about the fact.
        "You answer questions about product documentation using only the numbered context passages.\n"
        "- Answer in sentences that state the facts in words. A citation alone is never an answer.\n"
        "- Only state what the passages say. Do not guess, generalise or use outside knowledge.\n"
        "- If the passages cover different versions, say which version each fact applies to.\n"
        # The example sentence matters: without it gemma3:4b named versions less often and stacked citations again.
        '- End each sentence with its citation, e.g. "Feature X was removed in version 2.0 [2]." '
        "Cite only the passages that state that fact, each once.\n"
        "- If the passages do not contain the answer, reply exactly this, with no citation: " + NO_ANSWER + "\n"
        "- Be concise.",
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
