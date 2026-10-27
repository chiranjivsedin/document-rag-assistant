"""Generate an answer from retrieved chunks with the chat model."""

from dataclasses import dataclass

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel

from rag.generation.prompts import ANSWER_PROMPT, NO_ANSWER, format_context


@dataclass
class Answer:
    text: str
    sources: list[Document]  # the passages given to the model, in citation order ([1] = sources[0])


def answer(question: str, docs: list[Document], chat_model: BaseChatModel) -> Answer:
    """Answer from the given passages only; with no passages, say so without calling the model."""
    if not docs:
        return Answer(text=NO_ANSWER, sources=[])
    messages = ANSWER_PROMPT.invoke({"context": format_context(docs), "question": question})
    reply = chat_model.invoke(messages)
    return Answer(text=reply.content.strip(), sources=docs)
