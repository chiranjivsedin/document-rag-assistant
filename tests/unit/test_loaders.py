import hashlib
import os
from pathlib import Path

import pytest

from rag.ingestion.chunking import split_documents
from rag.ingestion.loaders import (
    DocumentsFolderUnreadable,
    drop_running_headers,
    file_metadata,
    is_link,
    iter_files,
    load_file,
)


def make_pdf(path: Path, pages: list[str]) -> None:
    """Write a minimal text PDF (one Helvetica line per page; "" = a page with no text)."""
    n = len(pages)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + b" ".join(f"{4 + 2 * i} 0 R".encode() for i in range(n)) + f"] /Count {n} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {5 + 2 * i} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


@pytest.fixture
def docs_dir(tmp_path):
    notes = tmp_path / "documentum" / "16.2" / "release-notes"
    notes.mkdir(parents=True)
    make_pdf(notes / "notes.pdf", ["Webtop is deprecated.", "", "Use D2 instead."])
    (notes / "summary.md").write_text("# Summary\n\nDeprecated features.", encoding="utf-8")
    (tmp_path / "readme.txt").write_text("Top-level note.", encoding="utf-8")
    (tmp_path / "documentum" / "image.png").write_bytes(b"not loaded")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "secret.txt").write_text("skip me", encoding="utf-8")
    return tmp_path


def test_iter_files_finds_supported_visible_files_in_order(docs_dir):
    found = [p.relative_to(docs_dir).as_posix() for p in iter_files(docs_dir)]
    assert found == [
        "documentum/16.2/release-notes/notes.pdf",
        "documentum/16.2/release-notes/summary.md",
        "readme.txt",
    ]


def test_pdf_gives_one_document_per_page_with_text(docs_dir):
    pdf = docs_dir / "documentum" / "16.2" / "release-notes" / "notes.pdf"
    docs = load_file(pdf, docs_dir)
    assert [d.metadata["page"] for d in docs] == [1, 3]  # page 2 has no text
    assert "Webtop is deprecated." in docs[0].page_content
    assert docs[0].metadata | {"page": None} == {
        "source": "documentum/16.2/release-notes/notes.pdf",
        "file_hash": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "product": "documentum",
        "version": "16.2",
        "doc_type": "release-notes",
        "page": None,
    }


WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]


def body(page: int) -> list[str]:
    """Eight lines of body text that differ from page to page."""
    return [f"{WORDS[(page + i) % len(WORDS)]} feature notes for page {WORDS[page % len(WORDS)]}" for i in range(8)]


def test_running_headers_are_removed_from_pdf_pages():
    # The Documentum 23.4 release notes put the page number first on even pages and last on odd ones.
    pages = ["\n".join([f"{n}   Documentum Server 23.4 Release Notes " if n % 2 == 0
                        else f"Documentum Server 23.4 Release Notes  {n} ", *body(n), " "]) for n in range(2, 8)]
    assert drop_running_headers(pages) == ["\n".join(body(n)) for n in range(2, 8)]


def test_running_footers_are_removed_too():
    # The Upgrade and Migration Guide's even pages end with the chapter name and the page number + guide code.
    pages = ["\n".join([*body(n), "Chapter 2   Upgrade and migration overview",
                        f"{n} OpenText Documentum System EDCCS230400-UMD-EN-01"]) for n in range(10, 16)]
    assert drop_running_headers(pages) == ["\n".join(body(n)) for n in range(10, 16)]


def test_a_line_repeated_in_the_middle_of_pages_is_kept():
    pages = ["\n".join([*body(n)[:4], "Note: back up the repository first.", *body(n)[4:]]) for n in range(6)]
    assert drop_running_headers(pages) == pages


def test_pdf_pages_are_chunked_as_one_text(tmp_path):
    make_pdf(tmp_path / "notes.pdf", ["8 Known issues", "TSERVER-471 Thumbnail server installation is limited."])
    chunks = split_documents(load_file(tmp_path / "notes.pdf", tmp_path), 1000, 150)
    assert [(c.page_content, c.metadata["page"]) for c in chunks] == [
        ("8 Known issues\nTSERVER-471 Thumbnail server installation is limited.", 1)]


def test_headers_need_several_pages():
    pages = ["\n".join(["Release Notes 1", *body(1)]), "\n".join(["Release Notes 2", *body(2)])]
    assert drop_running_headers(pages) == pages


def test_markdown_gives_one_document(docs_dir):
    docs = load_file(docs_dir / "documentum" / "16.2" / "release-notes" / "summary.md", docs_dir)
    assert len(docs) == 1
    assert docs[0].page_content.startswith("# Summary")
    assert docs[0].metadata["page"] is None


def test_missing_folder_levels_are_none(docs_dir):
    meta = file_metadata(docs_dir / "readme.txt", docs_dir)
    assert (meta["source"], meta["product"], meta["version"], meta["doc_type"]) == ("readme.txt", None, None, None)


def test_empty_text_file_gives_no_documents(tmp_path):
    (tmp_path / "empty.txt").write_text("   \n", encoding="utf-8")
    assert load_file(tmp_path / "empty.txt", tmp_path) == []


def test_unsupported_type_is_rejected(docs_dir):
    with pytest.raises(ValueError, match="Unsupported file type"):
        load_file(docs_dir / "documentum" / "image.png", docs_dir)


# Links: ingestion never reads a file through a link (symlink or Windows junction), nor a file with more than
# one hard link: either can bring in a file from outside the documents folder.

VISIBLE = ["documentum/16.2/release-notes/notes.pdf", "documentum/16.2/release-notes/summary.md", "readme.txt"]


def make_junction(link, target):
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, OSError):
        pytest.skip("can't create a junction here (not Windows)")


def make_symlink(link, target):
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:
        pytest.skip("this account can't create symlinks (Windows needs Developer Mode or admin rights)")


@pytest.fixture
def outside(tmp_path_factory):
    folder = tmp_path_factory.mktemp("outside")
    (folder / "16.2" / "release-notes").mkdir(parents=True)
    (folder / "16.2" / "release-notes" / "secret.md").write_text("outside the documents folder", encoding="utf-8")
    (folder / "lone.md").write_text("outside too", encoding="utf-8")
    return folder


def listed(docs_dir):
    return [p.relative_to(docs_dir).as_posix() for p in iter_files(docs_dir)]


@pytest.mark.parametrize("make_link", [make_junction, make_symlink])
def test_iter_files_never_enters_a_linked_folder(docs_dir, outside, make_link, caplog):
    make_link(docs_dir / "acme", outside)                              # at the top
    make_link(docs_dir / "documentum" / "16.3", outside / "16.2")      # and deeper down
    assert listed(docs_dir) == VISIBLE
    assert "acme" in caplog.text and "16.3" in caplog.text and "link" in caplog.text


def test_iter_files_skips_a_linked_file(docs_dir, outside, caplog):
    make_symlink(docs_dir / "documentum" / "linked.md", outside / "lone.md")
    assert listed(docs_dir) == VISIBLE
    assert "linked.md" in caplog.text


def test_iter_files_skips_files_with_more_than_one_hard_link(docs_dir, outside, caplog):
    os.link(outside / "lone.md", docs_dir / "documentum" / "hard.md")
    assert listed(docs_dir) == VISIBLE
    assert "hard.md" in caplog.text and "2 hard links" in caplog.text


def test_junctions_are_detected_without_path_is_junction(docs_dir, outside, monkeypatch):
    """Python 3.10 and 3.11 have no Path.is_junction: the reparse tag from lstat tells the same."""
    from rag.dms.storage import DocumentStorage, UnsafePath

    make_junction(docs_dir / "acme", outside)
    owner = next((c for c in type(docs_dir).__mro__ if "is_junction" in vars(c)), None)
    if owner is not None:
        monkeypatch.delattr(owner, "is_junction")
    assert not hasattr(docs_dir, "is_junction")

    assert is_link(docs_dir / "acme")
    assert not any(is_link(docs_dir / name) for name in ("documentum", "readme.txt", "missing"))
    assert listed(docs_dir) == VISIBLE                                   # ingestion
    with pytest.raises(UnsafePath):                                       # and the document library
        DocumentStorage(docs_dir, max_bytes=1024).path_of("acme/16.2/release-notes/secret.md")


# The documents folder itself: missing or unreadable is an error, never "empty" (which would empty the index).

def refuse_listing(monkeypatch, folder):
    real_iterdir = Path.iterdir

    def iterdir(self):
        if self == folder:
            raise PermissionError(13, "Access is denied", str(self))
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)


def test_a_missing_documents_folder_is_an_error_not_an_empty_folder(tmp_path):
    with pytest.raises(DocumentsFolderUnreadable, match="doesn't exist"):
        listed(tmp_path / "missing")
    (tmp_path / "a-file.txt").write_text("not a folder")
    with pytest.raises(DocumentsFolderUnreadable):
        listed(tmp_path / "a-file.txt")


def test_a_documents_folder_that_cant_be_listed_is_an_error(docs_dir, monkeypatch):
    refuse_listing(monkeypatch, docs_dir)
    with pytest.raises(DocumentsFolderUnreadable, match="Access is denied") as raised:
        listed(docs_dir)
    # Not an OSError on purpose: the API reports those as Ollama or the database being unreachable.
    assert not isinstance(raised.value, OSError)


def test_a_subfolder_that_cant_be_listed_is_skipped_and_logged(docs_dir, monkeypatch, caplog):
    refuse_listing(monkeypatch, docs_dir / "documentum")
    assert listed(docs_dir) == ["readme.txt"]
    assert "documentum" in caplog.text and "can't be read" in caplog.text


class RecordingStore:
    """Records what the pipeline asks the vector store to do."""

    def __init__(self):
        self.added, self.deleted = [], []

    def add_documents(self, docs):
        self.added.extend(docs)

    def delete(self, filter):
        self.deleted.append(filter["source"])


def test_ingestion_changes_nothing_when_the_documents_folder_cant_be_listed(docs_dir, monkeypatch):
    from rag.config import get_settings
    from rag.ingestion.pipeline import ingest

    store = RecordingStore()
    indexed = {source: ("hash", "nomic-embed-text") for source in VISIBLE}
    refuse_listing(monkeypatch, docs_dir)
    with pytest.raises(DocumentsFolderUnreadable):
        ingest(docs_dir, store, indexed, get_settings({}))
    assert store.deleted == [] and store.added == []


def test_a_documents_folder_that_is_itself_a_link_is_still_read(docs_dir, tmp_path_factory):
    # DOCUMENTS_DIR is configuration: refusing it would make every file look gone and empty the index.
    link = tmp_path_factory.mktemp("elsewhere") / "documents"
    make_junction(link, docs_dir)
    assert listed(link) == VISIBLE


def test_content_reached_through_a_link_is_removed_from_the_index(tmp_path, outside):
    """A file indexed before its folder became a link is no longer listed, so the next run removes its chunks."""
    from rag.config import get_settings
    from rag.ingestion.pipeline import ingest

    make_junction(tmp_path / "acme", outside)
    store = RecordingStore()
    indexed = {"acme/16.2/release-notes/secret.md": ("hash", "nomic-embed-text")}
    report = ingest(tmp_path, store, indexed, get_settings({}))
    assert report.removed == ["acme/16.2/release-notes/secret.md"]
    assert store.added == []
