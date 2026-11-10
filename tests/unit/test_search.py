from langchain_core.documents import Document

from rag.retrieval.search import Hybrid, Preference, choose, fuse, retrieve


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
    assert store.calls == [{"query": "What is deprecated?", "k": 3, "filter": None}]  # DEFAULT_K


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


# Hybrid search: vector and keyword rankings merged with Reciprocal Rank Fusion. In the live test the chunk stating
# that a client library "is no longer installed with the server" ranked 85th by vector distance alone.


def doc(name, distance, doc_type="guide", doc_id=None):
    return Document(id=doc_id or name, page_content=name, metadata={"doc_type": doc_type, "distance": distance})


def test_fuse_adds_reciprocal_ranks_over_the_rankings():
    a, b, c = doc("a", 0.2), doc("b", 0.3), doc("c", 0.5)
    fused = fuse([[a, b], [c, b]], rrf_k=10)
    # b: 1/12 + 1/12; a: 1/11; c: 1/11 (tie broken by the smaller distance)
    assert names(fused) == ["b", "a", "c"]


def test_fuse_keeps_the_first_rankings_document_and_matches_by_id():
    from_vector = doc("same chunk", 0.25, doc_id="id-1")
    from_keywords = Document(id="id-1", page_content="same chunk", metadata={"distance": 0.25, "other": True})
    fused = fuse([[from_vector], [from_keywords]], rrf_k=10)
    assert fused == [from_vector]


def test_fuse_without_ids_matches_by_source_and_text():
    first = Document(page_content="x", metadata={"source": "s", "distance": 0.1})
    second = Document(page_content="x", metadata={"source": "s", "distance": 0.1})
    other_file = Document(page_content="x", metadata={"source": "t", "distance": 0.1})
    assert len(fuse([[first], [second, other_file]], rrf_k=10)) == 2


def test_fuse_with_a_small_k_lets_a_keyword_only_top_hit_beat_weak_agreement():
    vector = [doc(f"v{i}", 0.2 + i / 100) for i in range(20)]
    weak_both = vector[15]
    keyword = [doc("fact", 0.40)] + [doc(f"k{i}", 0.5) for i in range(14)] + [weak_both]
    small = names(fuse([vector, keyword], rrf_k=10))
    assert small.index("fact") < small.index("v15")
    textbook = names(fuse([vector, keyword], rrf_k=60))  # with 60, anything both searches found outranks it
    assert textbook.index("fact") > textbook.index("v15")


def test_fuse_of_nothing_is_empty():
    assert fuse([[], []], rrf_k=10) == []


class FakeHybridStore:
    class Embeddings:
        def __init__(self):
            self.embedded = []

        def embed_query(self, text):
            self.embedded.append(text)
            return [0.5, 0.5]

    def __init__(self, results):
        self.results = results
        self.embeddings = self.Embeddings()
        self.calls = []

    def similarity_search_with_score_by_vector(self, embedding, k, filter):
        self.calls.append({"embedding": embedding, "k": k, "filter": filter})
        return self.results

    def similarity_search_with_score(self, query, k, filter):
        raise AssertionError("hybrid search embeds the question once and searches by vector")


class FakeKeywordSearch:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def __call__(self, question, embedding, filters, limit):
        self.calls.append({"question": question, "embedding": embedding, "filters": filters, "limit": limit})
        return self.hits


def vector_results(count, doc_type="guide"):
    return [(Document(id=f"v{i}", page_content=f"v{i}", metadata={"doc_type": doc_type}), 0.2 + i / 100)
            for i in range(count)]


def test_hybrid_embeds_once_and_passes_the_same_filters_and_depth_to_both_searches():
    store, keywords = FakeHybridStore(vector_results(3)), FakeKeywordSearch([])
    retrieve(store, "Is DFS installed?", k=2, version="23.4", product="documentum", hybrid=Hybrid(keywords))
    assert store.embeddings.embedded == ["Is DFS installed?"]
    assert store.calls == [{"embedding": [0.5, 0.5], "k": 8, "filter": {"version": "23.4", "product": "documentum"}}]
    assert keywords.calls == [{"question": "Is DFS installed?", "embedding": [0.5, 0.5],
                               "filters": {"version": "23.4", "product": "documentum"}, "limit": 8}]


def test_hybrid_without_filters_passes_none_to_the_store_and_an_empty_dict_to_the_keyword_search():
    store, keywords = FakeHybridStore([]), FakeKeywordSearch([])
    assert retrieve(store, "q", hybrid=Hybrid(keywords)) == []
    assert store.calls[0]["filter"] is None
    assert keywords.calls[0]["filters"] == {}


def test_hybrid_returns_k_passages_with_a_keyword_only_hit_and_its_own_distance():
    fact = doc("fact", 0.396, doc_id="fact")
    store, keywords = FakeHybridStore(vector_results(20)), FakeKeywordSearch([fact])
    docs = retrieve(store, "q", k=5, hybrid=Hybrid(keywords))
    assert len(docs) == 5
    assert names(docs) == ["v0", "fact", "v1", "v2", "v3"]  # 1/11 each for v0 and fact: v0 is closer
    assert docs[1].metadata["distance"] == 0.396
    assert [d.metadata["found_by"] for d in docs] == ["vector", "keyword", "vector", "vector", "vector"]


def test_hybrid_marks_passages_both_searches_found():
    store = FakeHybridStore(vector_results(3))
    keywords = FakeKeywordSearch([Document(id="v2", page_content="v2", metadata={"distance": 0.22})])
    docs = retrieve(store, "q", k=3, hybrid=Hybrid(keywords))
    assert names(docs)[0] == "v2"
    assert docs[0].metadata["found_by"] == "both"


def test_hybrid_then_preference_works_on_the_fused_order():
    # Fused: v0, kw-guide (keyword #1), v1, notes (keyword #2, within the gap), v2, ...
    keyword = [doc("kw-guide", 0.30), doc("notes", 0.33, doc_type="release-notes")]
    store, keywords = FakeHybridStore(vector_results(20)), FakeKeywordSearch(keyword)
    docs = retrieve(store, "q", k=3, prefer=NOTES, hybrid=Hybrid(keywords))
    assert names(docs) == ["v0", "kw-guide", "notes"]  # the last of the top 3 gave its place; fused order kept


def test_preference_gap_is_measured_from_the_closest_candidate_not_the_first():
    # A keyword hit leads the fused list but is far; the gap still counts from the closest passage (0.20).
    candidates = [doc("kw", 0.50), doc("v0", 0.20), doc("v1", 0.21), doc("notes", 0.40, doc_type="release-notes")]
    assert names(choose(candidates, 3, NOTES)) == ["kw", "v0", "v1"]  # 0.40 is more than 0.15 behind 0.20
    assert names(choose(candidates, 3, Preference("release-notes", 2, 0.25))) == ["kw", "v0", "notes"]
