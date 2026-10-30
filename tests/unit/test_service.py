import pytest
from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from rag import service
from rag.config import get_settings
from rag.generation.prompts import NO_ANSWER
from rag.service import Services, ask, build_services, run_ingest, sources_to_show

SETTINGS = get_settings({"DATABASE_URL": "postgresql://rag:secret@127.0.0.1:5433/rag", "VECTOR_TABLE": "chunks"})

PASSAGES = [
    (Document(page_content="Classic Desktop is deprecated.", metadata={"source": "acme/1.0/deprecated.md"}), 0.2),
    (Document(page_content="Use Acme Web instead.", metadata={"source": "acme/1.0/notes.md"}), 0.4),
]


class FakeStore:
    """Answers searches with fixed results and records what ingestion stores."""

    def __init__(self, results=()):
        self.results = list(results)
        self.searches = []
        self.added = []

    def similarity_search_with_score(self, query, k, filter):
        self.searches.append({"query": query, "k": k, "filter": filter})
        return self.results

    def add_documents(self, docs):
        self.added.extend(docs)

    def delete(self, filter):
        pass


def services_with(store, *replies):
    chat_model = FakeListChatModel(responses=list(replies)) if replies else None
    return Services(settings=SETTINGS, store=store, chat_model=chat_model)


def test_build_services_wires_store_and_chat_model(monkeypatch):
    monkeypatch.setattr(service, "get_embeddings", lambda settings: "embeddings")
    monkeypatch.setattr(service, "get_vector_store", lambda settings, embeddings: f"store({embeddings})")
    monkeypatch.setattr(service, "get_chat_model", lambda settings: "chat model")
    built = build_services(SETTINGS)
    assert built == Services(settings=SETTINGS, store="store(embeddings)", chat_model="chat model")


def test_build_services_for_ingest_skips_the_chat_model(monkeypatch):
    monkeypatch.setattr(service, "get_embeddings", lambda settings: "embeddings")
    monkeypatch.setattr(service, "get_vector_store", lambda settings, embeddings: "store")

    def no_chat_model(settings):
        raise AssertionError("chat model should not be built")

    monkeypatch.setattr(service, "get_chat_model", no_chat_model)
    assert build_services(SETTINGS, with_chat_model=False).chat_model is None


def test_run_ingest_compares_against_what_is_already_indexed(tmp_path, monkeypatch):
    (tmp_path / "acme" / "1.0" / "release-notes").mkdir(parents=True)
    (tmp_path / "acme" / "1.0" / "release-notes" / "notes.md").write_text("Feature X is new.", encoding="utf-8")
    lookups = []

    def fake_indexed_files(database_url, table):
        lookups.append((database_url, table))
        return {"acme/1.0/old.md": ("hash", SETTINGS.embedding_model)}

    monkeypatch.setattr(service, "indexed_files", fake_indexed_files)
    store = FakeStore()
    report = run_ingest(services_with(store), tmp_path)

    assert lookups == [(SETTINGS.database_url, "chunks")]
    assert report.added == ["acme/1.0/release-notes/notes.md"]
    assert report.removed == ["acme/1.0/old.md"]
    assert report.chunks_stored == len(store.added) == 1


def test_ask_answers_from_retrieved_passages():
    store = FakeStore(PASSAGES)
    result = ask(services_with(store, "Classic Desktop is deprecated [1]."), "What is deprecated?",
                 k=3, version="1.0", product="acme")
    assert result.text == "Classic Desktop is deprecated [1]."
    assert [doc.page_content for doc in result.sources] == ["Classic Desktop is deprecated.", "Use Acme Web instead."]
    assert [doc.metadata["distance"] for doc in result.sources] == [0.2, 0.4]
    assert store.searches == [{"query": "What is deprecated?", "k": 3, "filter": {"version": "1.0", "product": "acme"}}]


def test_ask_hides_sources_when_the_model_does_not_know():
    result = ask(services_with(FakeStore(PASSAGES), NO_ANSWER), "Unrelated?")
    assert result.text == NO_ANSWER
    assert result.sources == []


def test_ask_treats_a_citation_only_reply_as_no_answer():
    result = ask(services_with(FakeStore(PASSAGES), "[1]"), "Which version added a mobile app?")
    assert result.text == NO_ANSWER
    assert result.sources == []


def test_ask_with_nothing_retrieved_says_so_without_the_model():
    result = ask(services_with(FakeStore([]), "should not be used"), "Anything?")
    assert result.text == NO_ANSWER
    assert result.sources == []


def test_ask_needs_a_chat_model():
    with pytest.raises(ValueError, match="chat model"):
        ask(services_with(FakeStore(PASSAGES)), "Q?")


def test_sources_to_show():
    docs = [doc for doc, _ in PASSAGES]
    assert sources_to_show("X is deprecated [1].", docs) == docs
    assert sources_to_show(NO_ANSWER, docs) == []
    assert sources_to_show("Sorry. " + NO_ANSWER, docs) == []
    assert sources_to_show("Anything.", []) == []
