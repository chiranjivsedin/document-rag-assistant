import hashlib
import io

import pytest
from pypdf import PdfWriter

from rag.dms import storage as storage_module
from rag.dms.storage import (
    DocumentStorage,
    DocumentTooLarge,
    InvalidDocument,
    UnsafePath,
    check_folder_name,
    clean_filename,
)

MB = 1024 * 1024


def pdf_bytes() -> bytes:
    """A real, readable PDF (one blank page)."""
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def docs(tmp_path):
    return DocumentStorage(tmp_path, max_bytes=MB)


def temp_files(docs):
    temp = docs.documents_dir / ".uploads"
    return list(temp.iterdir()) if temp.exists() else []


# Folder names (product, version, doc_type)

@pytest.mark.parametrize("value, expected", [
    ("documentum", "documentum"), ("16.2", "16.2"), ("release-notes", "release-notes"), ("v_1", "v_1"),
    ("  Acme  ", "Acme"), ("x", "x"),
])
def test_valid_folder_names(value, expected):
    assert check_folder_name(value, "product") == expected


@pytest.mark.parametrize("value", [
    "", "   ", "..", "../x", "a/b", "a\\b", ".hidden", "x.", "-x", "16..2", "a b", "café", "CON", "lpt1", "nul.txt",
    "a" * 65,
])
def test_invalid_folder_names(value):
    with pytest.raises(InvalidDocument, match="version"):
        check_folder_name(value, "version")


# File names

@pytest.mark.parametrize("original, expected", [
    ("notes.pdf", "notes.pdf"),
    ("Notes (Final) v2.PDF", "Notes (Final) v2.pdf"),
    ("C:\\fakepath\\notes.md", "notes.md"),  # what browsers send for some uploads
    ("../../etc/passwd.txt", "passwd.txt"),
    ("résumé.md", "resume.md"),
    ("a<b>c?|*.txt", "a_b_c_.txt"),
    ("  spaced   name  .txt", "spaced name.txt"),
    (".pdf", "document.pdf"),
    ("日本語.pdf", "document.pdf"),
    ("CON.txt", "_CON.txt"),
    ("x" * 300 + ".pdf", "x" * 116 + ".pdf"),
])
def test_clean_filename(original, expected):
    assert clean_filename(original) == expected


@pytest.mark.parametrize("original", ["notes.docx", "notes", "notes.pdf.exe", "", "archive.zip"])
def test_unsupported_types_are_rejected(original):
    with pytest.raises(InvalidDocument, match="Only .md, .pdf, .txt files can be uploaded"):
        clean_filename(original)


# Paths

def test_source_for_builds_the_folder_layout(docs):
    assert docs.source_for("documentum", "16.2", "release-notes", "notes.pdf") == "documentum/16.2/release-notes/notes.pdf"


def test_source_for_reuses_existing_names_that_differ_in_capitals(docs):
    (docs.documents_dir / "Documentum" / "16.2").mkdir(parents=True)
    # Windows would put the file in the existing folder; the source must match what ingestion will read.
    assert docs.source_for("documentum", "16.2", "release-notes", "notes.pdf") == "Documentum/16.2/release-notes/notes.pdf"


def test_source_for_rejects_paths_too_long_for_windows(docs, monkeypatch):
    monkeypatch.setattr(storage_module, "MAX_PATH_CHARS", len(str(docs.documents_dir.resolve())) + 20)
    with pytest.raises(InvalidDocument, match="too long"):
        docs.source_for("documentum", "16.2", "release-notes", "notes.pdf")


@pytest.mark.parametrize("source", ["../outside.pdf", "a/../../outside.pdf", ".uploads/x.pdf", "a/.hidden/x.pdf", "."])
def test_path_of_refuses_anything_outside_the_documents_folder(docs, source):
    with pytest.raises(UnsafePath):
        docs.path_of(source)


def test_path_of_refuses_absolute_paths(docs, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "x.pdf"
    with pytest.raises(UnsafePath):
        docs.path_of(str(outside))


# Saving

def test_save_writes_the_file_and_reports_size_and_hash(docs):
    data = pdf_bytes()
    saved = docs.save(io.BytesIO(data), "documentum/16.2/release-notes/notes.pdf")

    path = docs.documents_dir / "documentum" / "16.2" / "release-notes" / "notes.pdf"
    assert path.read_bytes() == data
    assert saved.size_bytes == len(data)
    assert saved.file_hash == hashlib.sha256(data).hexdigest()
    assert temp_files(docs) == []


def test_save_accepts_utf8_text_with_or_without_bom(docs):
    docs.save(io.BytesIO("Webtop – déprécié €".encode()), "a/1/notes/plain.txt")
    docs.save(io.BytesIO("\ufeff# Notes".encode()), "a/1/notes/bom.md")
    assert docs.exists("a/1/notes/plain.txt") and docs.exists("a/1/notes/bom.md")


@pytest.mark.parametrize("source, data, message", [
    ("a/1/notes/x.pdf", b"", "empty"),
    ("a/1/notes/x.pdf", b"just text, not a PDF", "not a PDF"),
    ("a/1/notes/x.pdf", b"%PDF-1.4\nthis is not really a PDF", "can't be read"),
    ("a/1/notes/x.txt", "café".encode("latin-1"), "UTF-8"),
    ("a/1/notes/x.md", b"MZ\x90\x00\x03 binary", "not plain text"),
])
def test_save_rejects_bad_content_and_leaves_nothing_behind(docs, source, data, message):
    with pytest.raises(InvalidDocument, match=message):
        docs.save(io.BytesIO(data), source)
    assert not docs.exists(source)
    assert temp_files(docs) == []


def test_save_enforces_the_size_limit_while_reading(docs):
    with pytest.raises(DocumentTooLarge, match="1 MB limit"):
        docs.save(io.BytesIO(b"x" * (MB + 1)), "a/1/notes/big.txt")
    assert not docs.exists("a/1/notes/big.txt")
    assert temp_files(docs) == []


def test_save_accepts_exactly_the_limit(docs):
    assert docs.save(io.BytesIO(b"x" * MB), "a/1/notes/max.txt").size_bytes == MB


def test_save_never_overwrites_an_existing_file(docs):
    docs.save(io.BytesIO(b"original"), "a/1/notes/x.txt")
    with pytest.raises(FileExistsError):
        docs.save(io.BytesIO(b"replacement"), "a/1/notes/x.txt")
    assert docs.path_of("a/1/notes/x.txt").read_bytes() == b"original"
    assert temp_files(docs) == []


def test_saving_in_progress_is_invisible_to_ingestion(docs):
    from rag.ingestion.loaders import iter_files

    (docs.documents_dir / ".uploads").mkdir()
    (docs.documents_dir / ".uploads" / "half-written.pdf").write_bytes(b"%PDF-")
    assert list(iter_files(docs.documents_dir)) == []


# Deleting

def test_delete_removes_the_file_and_reports_a_missing_one(docs):
    docs.save(io.BytesIO(b"text"), "a/1/notes/x.txt")
    assert docs.delete("a/1/notes/x.txt") is True
    assert docs.delete("a/1/notes/x.txt") is False
    assert (docs.documents_dir / "a" / "1" / "notes").is_dir()  # folders are left in place


def test_delete_refuses_paths_outside_the_documents_folder(docs):
    with pytest.raises(UnsafePath):
        docs.delete("../outside.txt")
