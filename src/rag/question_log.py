"""The question log: one JSON line per question asked through the API's /chat.

It records what people ask and what happened, so a wrong or slow answer can be looked into afterwards: the
question and its filters, the outcome and the time it took, the answer, and every passage the model was given
(also for an "I don't know" reply, when the web app shows none), with which search found it and whether the
answer cited it. Off unless QUESTION_LOG names a file; nothing leaves the machine.

Writing it never fails a request: a file that can't be written is reported once in the server log and skipped.
The file holds questions and answers in plain text, so it lives with the server's other logs and is as
confidential as the documents. It only grows: archive or truncate it with the other logs.
"""

import json
import logging
import threading
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.documents import Document

from rag.generation.citations import cited_numbers

logger = logging.getLogger(__name__)

# An answer longer than this is cut in the log (the model's replies are normally a few hundred characters).
MAX_ANSWER_CHARS = 4000


class QuestionLog:
    """Appends entries to a file as JSON lines. Safe to share between the API's request threads."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._warned = False

    def write(self, entry: dict) -> None:
        line = json.dumps(entry, ensure_ascii=False, default=str)
        try:
            with self._lock, open(self.path, "a", encoding="utf-8") as file:
                file.write(line + "\n")
        except OSError as error:
            if not self._warned:  # once: a missing folder or a permission problem would repeat on every question
                self._warned = True
                logger.warning("Couldn't write the question log %s (answers are not affected): %s", self.path, error)


def log_entry(
    *,
    question: str,
    product: str | None,
    version: str | None,
    k: int,
    model: str,
    outcome: str,
    status: int,
    elapsed_ms: int,
    answer: str | None = None,
    passages: Sequence[Document] = (),
    error: str | None = None,
    now: datetime | None = None,
) -> dict:
    """One log entry. `outcome`: "answered", "no_answer", "unavailable" or "error"; `passages`: every passage given to
    the model, numbered as the answer cites them; `error`: a client-safe reason (never an exception's message)."""
    cited = cited_numbers(answer or "")
    return {
        "time": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "question": question,
        "product": product,
        "version": version,
        "k": k,
        "model": model,
        "outcome": outcome,
        "status": status,
        "elapsed_ms": elapsed_ms,
        "answer": None if answer is None else answer[:MAX_ANSWER_CHARS],
        "error": error,
        "passages": [_passage(number, doc, number in cited) for number, doc in enumerate(passages, 1)],
    }


def _passage(number: int, doc: Document, cited: bool) -> dict:
    meta = doc.metadata
    distance = meta.get("distance")
    return {
        "n": number,
        "source": meta.get("source"),
        "page": meta.get("page"),
        "doc_type": meta.get("doc_type"),
        "distance": None if distance is None else round(float(distance), 4),
        "found_by": meta.get("found_by"),  # "vector", "keyword" or "both" with hybrid search; absent without it
        "cited": cited,
    }
