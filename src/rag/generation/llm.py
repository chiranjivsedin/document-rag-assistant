"""Generate an answer from retrieved chunks with the chat model."""

from collections.abc import Iterator
from dataclasses import dataclass

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable

from rag.generation.prompts import ANSWER_PROMPT, NO_ANSWER, format_context


@dataclass
class Answer:
    text: str
    sources: list[Document]  # the passages given to the model, in citation order ([1] = sources[0])


def answer_chain(chat_model: BaseChatModel) -> Runnable:
    """LCEL chain: {"context", "question"} -> prompt -> chat model -> plain text."""
    return ANSWER_PROMPT | chat_model | StrOutputParser()


def answer(question: str, docs: list[Document], chat_model: BaseChatModel) -> Answer:
    """Answer from the given passages only; with no passages, say so without calling the model."""
    if not docs:
        return Answer(text=NO_ANSWER, sources=[])
    text = answer_chain(chat_model).invoke(_inputs(question, docs))
    return Answer(text=text.strip(), sources=docs)


def stream_answer(question: str, docs: list[Document], chat_model: BaseChatModel) -> Iterator[str]:
    """Same as answer(), but yields the text piece by piece as the model writes it."""
    if not docs:
        yield NO_ANSWER
        return
    yield from answer_chain(chat_model).stream(_inputs(question, docs))


def _inputs(question: str, docs: list[Document]) -> dict:
    return {"context": format_context(docs), "question": question}
