import json
import logging
from datetime import datetime, timezone

from langchain_core.documents import Document

from rag.question_log import MAX_ANSWER_CHARS, QuestionLog, log_entry

NOW = datetime(2026, 11, 11, 10, 30, 5, tzinfo=timezone.utc)
PASSAGES = [
    Document(page_content="Classic Desktop is deprecated.", metadata={
        "source": "acme/1.0/release-notes/notes.pdf", "page": 3, "doc_type": "release-notes",
        "distance": 0.213456, "found_by": "both", "product": "acme", "chunk": 0}),
    Document(page_content="Use Acme Web.", metadata={
        "source": "acme/1.0/guide/guide.md", "page": None, "doc_type": "guide", "distance": 0.4}),
    Document(page_content="Unrelated.", metadata={"source": "acme/1.0/guide/guide.md", "distance": 0.5}),
]


def entry(**overrides):
    values = dict(question="What is deprecated?", product="acme", version="1.0", k=3, model="gemma3:4b",
                  outcome="answered", status=200, elapsed_ms=1234, answer="Classic Desktop is deprecated [1][3].",
                  passages=PASSAGES, now=NOW)
    values.update(overrides)
    return log_entry(**values)


def test_an_entry_holds_the_question_the_outcome_and_every_passage():
    assert entry() == {
        "time": "2026-11-11T10:30:05+00:00",
        "question": "What is deprecated?", "product": "acme", "version": "1.0", "k": 3, "model": "gemma3:4b",
        "outcome": "answered", "status": 200, "elapsed_ms": 1234,
        "answer": "Classic Desktop is deprecated [1][3].", "error": None,
        "passages": [
            {"n": 1, "source": "acme/1.0/release-notes/notes.pdf", "page": 3, "doc_type": "release-notes",
             "distance": 0.2135, "found_by": "both", "cited": True},
            {"n": 2, "source": "acme/1.0/guide/guide.md", "page": None, "doc_type": "guide",
             "distance": 0.4, "found_by": None, "cited": False},
            {"n": 3, "source": "acme/1.0/guide/guide.md", "page": None, "doc_type": None,
             "distance": 0.5, "found_by": None, "cited": True},
        ],
    }


def test_citation_groups_and_ranges_mark_passages_as_cited():
    cited = [p["cited"] for p in entry(answer="A [1, 2]. B [2-3].")["passages"]]
    assert cited == [True, True, True]


def test_an_error_entry_has_no_answer_and_no_passages():
    e = entry(outcome="unavailable", status=503, answer=None, passages=(), error="Can't reach Ollama.")
    assert (e["answer"], e["passages"], e["error"]) == (None, [], "Can't reach Ollama.")


def test_a_long_answer_is_cut():
    assert len(entry(answer="x" * (MAX_ANSWER_CHARS + 50))["answer"]) == MAX_ANSWER_CHARS


def test_entries_are_appended_as_json_lines(tmp_path):
    path = tmp_path / "questions.jsonl"
    log = QuestionLog(path)
    log.write(entry(question="Größe des Index?"))
    log.write(entry(outcome="no_answer"))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["outcome"] for line in lines] == ["answered", "no_answer"]
    assert "Größe des Index?" in lines[0]  # kept as written, not escaped


def test_a_log_that_cant_be_written_warns_once_and_never_raises(tmp_path, caplog):
    log = QuestionLog(tmp_path / "no-such-folder" / "questions.jsonl")
    with caplog.at_level(logging.WARNING, logger="rag.question_log"):
        log.write(entry())
        log.write(entry())
    assert len([r for r in caplog.records if "question log" in r.getMessage()]) == 1
