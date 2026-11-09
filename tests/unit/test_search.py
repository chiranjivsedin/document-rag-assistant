from langchain_core.documents import Document

from rag.retrieval.search import Preference, retrieve


class FakeStore:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def similarity_search_with_score(self, query, k, filter):
        self.calls.append({"query": query, "k": k, "filter": filter})
        return self.results


def test_no_filters_passes_none():
    store = FakeStore([])
    retrieve(store, "What is deprecated?")
    assert store.calls == [{"query": "What is deprecated?", "k": 5, "filter": None}]


def test_version_and_product_filters():
    store = FakeStore([])
    retrieve(store, "q", k=3, version="16.2", product="documentum")
    assert store.calls[0]["filter"] == {"version": "16.2", "product": "documentum"}
    assert store.calls[0]["k"] == 3


def test_empty_filter_values_are_ignored():
    store = FakeStore([])
    retrieve(store, "q", version="", product=None)
    assert store.calls[0]["filter"] is None


def test_results_keep_order_and_carry_distance():
    store = FakeStore([
        (Document(page_content="closest", metadata={"version": "1.0"}), 0.2),
        (Document(page_content="further", metadata={"version": "1.0"}), 0.5),
    ])
    docs = retrieve(store, "q")
    assert [d.page_content for d in docs] == ["closest", "further"]
    assert [d.metadata["distance"] for d in docs] == [0.2, 0.5]
    assert docs[0].metadata["version"] == "1.0"


# Release notes kept in view: in the live test the PostgreSQL answer was in the Release Notes, but the top 5 were
# all Upgrade and Fundamentals Guide passages (1,251 chunks, most from the two large guides).

NOTES = Preference(doc_type="release-notes", slots=2, max_gap=0.15)


def passage(name, doc_type, distance):
    return Document(page_content=name, metadata={"doc_type": doc_type}), distance


def guides_then_notes():
    return [passage(f"guide {i}", "upgrade-guide", 0.20 + i / 100) for i in range(6)] + [
        passage("notes A", "release-notes", 0.27),
        passage("guide far", "fundamentals-guide", 0.28),
        passage("notes B", "release-notes", 0.30),
        passage("notes C", "release-notes", 0.31),
    ]


def names(docs):
    return [d.page_content for d in docs]


def test_a_preference_fetches_more_candidates_but_returns_k():
    store = FakeStore(guides_then_notes())
    docs = retrieve(store, "q", k=5, prefer=NOTES)
    assert store.calls[0]["k"] == 20
    assert len(docs) == 5


def test_preferred_passages_replace_the_furthest_others_and_order_stays_by_distance():
    docs = retrieve(FakeStore(guides_then_notes()), "q", k=5, prefer=NOTES)
    assert names(docs) == ["guide 0", "guide 1", "guide 2", "notes A", "notes B"]
    assert [round(d.metadata["distance"], 2) for d in docs] == [0.20, 0.21, 0.22, 0.27, 0.30]


def test_only_passages_within_the_gap_are_promoted():
    docs = retrieve(FakeStore(guides_then_notes()), "q", k=5, prefer=Preference("release-notes", 2, max_gap=0.08))
    assert names(docs) == ["guide 0", "guide 1", "guide 2", "guide 3", "notes A"]  # notes B is 0.10 behind


def test_nothing_changes_when_enough_preferred_passages_are_already_in_the_top_k():
    results = [passage("notes A", "release-notes", 0.1), passage("notes B", "release-notes", 0.2),
               passage("guide 0", "upgrade-guide", 0.3), passage("notes C", "release-notes", 0.31)]
    assert names(retrieve(FakeStore(results), "q", k=3, prefer=NOTES)) == ["notes A", "notes B", "guide 0"]


def test_one_preferred_passage_in_the_top_k_leaves_one_place_to_fill():
    results = [passage("guide 0", "upgrade-guide", 0.1), passage("notes A", "release-notes", 0.2),
               passage("guide 1", "upgrade-guide", 0.21), passage("notes B", "release-notes", 0.22),
               passage("notes C", "release-notes", 0.23)]
    assert names(retrieve(FakeStore(results), "q", k=3, prefer=NOTES)) == ["guide 0", "notes A", "notes B"]


def test_no_preferred_candidates_means_plain_top_k():
    results = [passage(f"guide {i}", "upgrade-guide", 0.2 + i / 100) for i in range(10)]
    assert names(retrieve(FakeStore(results), "q", k=3, prefer=NOTES)) == ["guide 0", "guide 1", "guide 2"]


def test_fewer_candidates_than_k():
    results = [passage("guide 0", "upgrade-guide", 0.2), passage("notes A", "release-notes", 0.3)]
    assert names(retrieve(FakeStore(results), "q", k=5, prefer=NOTES)) == ["guide 0", "notes A"]
    assert retrieve(FakeStore([]), "q", k=5, prefer=NOTES) == []


def test_slots_never_exceed_k():
    results = [passage("guide 0", "upgrade-guide", 0.2), passage("notes A", "release-notes", 0.21),
               passage("notes B", "release-notes", 0.22)]
    assert names(retrieve(FakeStore(results), "q", k=1, prefer=Preference("release-notes", 3, 0.15))) == ["notes A"]


def test_zero_slots_is_off():
    store = FakeStore(guides_then_notes())
    docs = retrieve(store, "q", k=5, prefer=Preference("release-notes", 0, 0.15))
    assert store.calls[0]["k"] == 5
    assert names(docs) == [f"guide {i}" for i in range(5)]
