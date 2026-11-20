"""Live check: the tag tables, their seed and the repository against the local Docker PostgreSQL.

Uses its own tables (it_tags_*, it_tags_documents, it_tags_chunks); Ollama is not used. Needs `docker compose up -d`.
Run with: pytest -m integration
"""

import os

import psycopg
import pytest

from rag.dms.repository import DocumentRepository, NewDocument
from rag.dms.tags import SEED_VALUES, InvalidTag, TagConflict, TagInUse, TagNotFound, TagRepository

pytestmark = pytest.mark.integration

LOCAL_DB = "postgresql://rag:rag_local_dev@127.0.0.1:5433/rag"
PREFIX = "it_tags_"
DOCUMENTS_TABLE = "it_tags_documents"
CHUNKS_TABLE = "it_tags_chunks"


@pytest.fixture
def db_url():
    url = os.environ.get("DATABASE_URL", LOCAL_DB)

    def drop():
        with psycopg.connect(url, autocommit=True) as conn:
            for table in ("document_tags", "tag_values", "tag_categories"):
                conn.execute(f"DROP TABLE IF EXISTS {PREFIX}{table}")
            conn.execute(f"DROP TABLE IF EXISTS {DOCUMENTS_TABLE}")
            conn.execute(f"DROP TABLE IF EXISTS {CHUNKS_TABLE}")

    drop()
    yield url
    drop()


@pytest.fixture
def tags(db_url):
    repo = TagRepository(db_url, documents_table=DOCUMENTS_TABLE, prefix=PREFIX, chunks_table=CHUNKS_TABLE)
    repo.create_tables()
    repo.seed()
    return repo


@pytest.fixture
def documents(db_url, tags):
    return DocumentRepository(db_url, table=DOCUMENTS_TABLE)


def add_document(documents, name, product="content-management", version="24.4", doc_type="release-notes"):
    return documents.add(NewDocument(
        source=f"{product}/{version}/{doc_type}/{name}", filename=name, content_type="text/markdown",
        size_bytes=10, file_hash="a" * 64, product=product, version=version, doc_type=doc_type))


def category(tags, key):
    return next(c for c in tags.categories() if c.key == key)


def value(tags, key, code):
    return next(v for v in category(tags, key).values if v.code == code)


def test_seed_creates_the_three_builtins(tags):
    categories = tags.categories()
    assert [(c.key, c.label, c.builtin, c.multiple) for c in categories] == [
        ("product", "Product", True, False), ("version", "Version", True, False),
        ("doc_type", "Document type", True, False)]
    assert [(v.code, v.label) for v in categories[0].values] == [("content-management", "Content Management")]
    assert [v.code for v in categories[1].values] == [code for code, _ in SEED_VALUES["version"]]
    assert len(categories[1].values) == 31
    assert [(v.code, v.label) for v in categories[2].values] == SEED_VALUES["doc_type"]
    assert all(v.active for c in categories for v in c.values)


def test_seed_is_idempotent_and_keeps_admin_changes(tags):
    retired = tags.update_value(value(tags, "version", "16.2").id, active=False)
    relabelled = tags.update_value(value(tags, "doc_type", "release-notes").id, label="Release notes (all)")
    tags.create_tables()
    tags.seed()

    versions = category(tags, "version").values
    assert len(versions) == 31 and len({v.code for v in versions}) == 31
    assert value(tags, "version", "16.2") == retired and not retired.active
    assert value(tags, "doc_type", "release-notes") == relabelled
    assert relabelled.label == "Release notes (all)"
    assert len(tags.categories()) == 3


def test_existing_document_values_are_not_imported(tags, documents):
    add_document(documents, "a.md", product="acme", version="1.0", doc_type="guide")
    tags.seed()
    assert "acme" not in {v.code for v in category(tags, "product").values}
    assert not tags.is_active_code("product", "acme")
    assert tags.is_active_code("product", "content-management")


@pytest.mark.parametrize("code, label", [
    ("release-notes-2", "Release Notes"),     # label differs only in case
    ("release-notes-2", "  release   notes "),  # and in spacing
    ("Release-Notes", "Notes again"),          # code differs only in case
])
def test_duplicate_label_or_code_differing_only_in_case_is_refused(tags, code, label):
    with pytest.raises(TagConflict):
        tags.add_value("doc_type", code, label)
    assert len(category(tags, "doc_type").values) == 4


def test_duplicate_categories_are_refused(tags):
    tags.add_category("module", "Module", multiple=True)
    with pytest.raises(TagConflict):
        tags.add_category("module", "Other", multiple=False)
    with pytest.raises(TagConflict):
        tags.add_category("modules", " module ", multiple=False)
    with pytest.raises(TagConflict):
        tags.add_category("version", "Versions", multiple=False)


def test_add_value_validates_and_keeps_versions_in_numeric_order(tags):
    with pytest.raises(InvalidTag):
        tags.add_value("version", "../x", "x")
    with pytest.raises(TagNotFound):
        tags.add_value("nope", "a", "A")
    tags.add_value("version", "16.10", "16.10")
    tags.add_value("version", "latest", "latest")
    codes = [v.code for v in category(tags, "version").values]
    assert codes[codes.index("16.7") + 1] == "16.10"
    assert codes[-1] == "latest"
    tags.add_category("module", "Module", multiple=True)
    tags.add_value("module", "workflow", "Workflow")
    tags.add_value("module", "search", "Search")
    assert [v.code for v in category(tags, "module").values] == ["workflow", "search"]  # in the order added


def test_retired_value_is_kept_but_inactive(tags):
    retired = tags.update_value(value(tags, "version", "20.2").id, active=False)
    assert not retired.active and retired in category(tags, "version").values
    assert not tags.is_active_code("version", "20.2")
    # Its label is free again while it is retired; restoring it then clashes
    tags.add_value("version", "20.2.1", "20.2")
    with pytest.raises(TagConflict):
        tags.update_value(retired.id, active=True)
    with pytest.raises(TagNotFound):
        tags.update_value(999999, label="x")


def test_value_in_use_cannot_be_deleted(tags, documents, db_url):
    add_document(documents, "a.md", version="24.4")
    with pytest.raises(TagInUse):
        tags.delete_value(value(tags, "version", "24.4").id)

    with psycopg.connect(db_url, autocommit=True) as conn:  # a chunk still under a folder nobody tracks
        conn.execute(f"CREATE TABLE {CHUNKS_TABLE} (source text, product text, version text, doc_type text)")
        conn.execute(f"INSERT INTO {CHUNKS_TABLE} VALUES ('x', 'content-management', '25.1', 'release-notes')")
    with pytest.raises(TagInUse):
        tags.delete_value(value(tags, "version", "25.1").id)

    tags.add_category("module", "Module", multiple=False)
    used = tags.add_value("module", "workflow", "Workflow")
    document = add_document(documents, "b.md")
    tags.set_document_tags(document.id, [used.id])
    with pytest.raises(TagInUse):
        tags.delete_value(used.id)

    unused = value(tags, "version", "16.4")
    tags.delete_value(unused.id)
    assert "16.4" not in {v.code for v in category(tags, "version").values}
    with pytest.raises(TagNotFound):
        tags.delete_value(unused.id)


def test_set_document_tags_rules(tags, documents):
    document = add_document(documents, "a.md")
    tags.add_category("module", "Module", multiple=False)
    tags.add_category("audience", "Audience", multiple=True)
    workflow = tags.add_value("module", "workflow", "Workflow")
    search = tags.add_value("module", "search", "Search")
    admins = tags.add_value("audience", "admins", "Administrators")
    developers = tags.add_value("audience", "developers", "Developers")
    old = tags.add_value("audience", "old", "Old")
    tags.update_value(old.id, active=False)

    with pytest.raises(TagConflict):  # a built-in value
        tags.set_document_tags(document.id, [value(tags, "version", "24.4").id])
    with pytest.raises(TagConflict):  # a retired value, newly added
        tags.set_document_tags(document.id, [old.id])
    with pytest.raises(TagConflict):  # two values in a single-value category
        tags.set_document_tags(document.id, [workflow.id, search.id])
    with pytest.raises(TagNotFound):
        tags.set_document_tags(document.id, [999999])
    with pytest.raises(TagNotFound):
        import uuid
        tags.set_document_tags(uuid.uuid4(), [workflow.id])
    assert tags.document_tags([document.id]) == {}  # nothing changed by the refusals

    result = tags.set_document_tags(document.id, [workflow.id, admins.id, developers.id, admins.id])
    assert {v.code for v in result} == {"workflow", "admins", "developers"}
    assert tags.set_document_tags(document.id, [search.id]) == [tags.document_tags([document.id])[document.id][0]]
    assert [v.code for v in tags.document_tags([document.id])[document.id]] == ["search"]
    assert tags.set_document_tags(document.id, []) == []


def test_a_retired_value_a_document_already_has_may_stay(tags, documents):
    document = add_document(documents, "a.md")
    tags.add_category("audience", "Audience", multiple=True)
    admins = tags.add_value("audience", "admins", "Administrators")
    developers = tags.add_value("audience", "developers", "Developers")
    tags.set_document_tags(document.id, [admins.id])
    tags.update_value(admins.id, active=False)
    kept = tags.set_document_tags(document.id, [admins.id, developers.id])
    assert {(v.code, v.active) for v in kept} == {("admins", False), ("developers", True)}


def test_document_delete_cascades_its_tags(tags, documents, db_url):
    document = add_document(documents, "a.md")
    tags.add_category("module", "Module", multiple=False)
    workflow = tags.add_value("module", "workflow", "Workflow")
    tags.set_document_tags(document.id, [workflow.id])
    assert documents.delete(document.id)
    assert tags.document_tags([document.id]) == {}
    tags.delete_value(workflow.id)  # no longer in use


def test_sources_with_tags_requires_every_category(tags, documents):
    tags.add_category("module", "Module", multiple=True)
    tags.add_category("audience", "Audience", multiple=True)
    workflow = tags.add_value("module", "workflow", "Workflow")
    search = tags.add_value("module", "search", "Search")
    admins = tags.add_value("audience", "admins", "Administrators")
    a = add_document(documents, "a.md")
    b = add_document(documents, "b.md")
    c = add_document(documents, "c.md")
    tags.set_document_tags(a.id, [workflow.id, admins.id])
    tags.set_document_tags(b.id, [search.id])
    tags.set_document_tags(c.id, [admins.id])

    assert tags.sources_with_tags({"module": ["workflow", "search"]}) == {a.source, b.source}  # any value
    assert tags.sources_with_tags({"module": ["workflow", "search"], "audience": ["admins"]}) == {a.source}
    assert tags.sources_with_tags({"module": ["nothing"]}) == set()
    assert tags.sources_with_tags({"module": []}) == {a.source, b.source, c.source}  # no codes: no condition

    tags.update_value(search.id, active=False)  # retired values still filter
    assert tags.sources_with_tags({"module": ["search"]}) == {b.source}

    with pytest.raises(TagNotFound):
        tags.sources_with_tags({"nope": ["x"]})
    with pytest.raises(TagNotFound):
        tags.sources_with_tags({"product": ["content-management"]})


def test_value_deleted_during_an_update_is_not_found(tags, monkeypatch):
    # Another request deletes the value between update_value()'s first read and its update: 404, not a crash.
    gone = tags.add_value("doc_type", "short-lived", "Short-lived")
    tags.delete_value(gone.id)
    monkeypatch.setattr(tags, "_value", lambda value_id: gone)
    with pytest.raises(TagNotFound):
        tags.update_value(gone.id, label="Renamed")
