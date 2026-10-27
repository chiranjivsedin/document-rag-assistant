from langchain_core.documents import Document

from rag.retrieval.search import retrieve


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
