import hashlib
import io
import os

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


@pytest.mark.parametrize("source", [
    # traversal and hidden parts
    "../outside.pdf", "a/../../outside.pdf", "a/..", ".uploads/x.pdf", "a/.hidden/x.pdf", ".", "a/./b.pdf",
    ".. /x.txt", "a/.. /x.txt", "a/..../x.txt", "a//b.pdf", "a/",
    # absolute, UNC, drive and stream forms
    "/abs/x.txt", "//server/share/x.txt", "C:x.txt", "C:/x.txt", "a/b.pdf:hidden", "a/b.pdf::$DATA",
    "..\\x.txt", "a\\b.pdf",
    # names Windows changes or treats as devices
    "a/x.txt.", "a/x.txt ", "a./x.txt", "a /x.txt", "a/CON", "a/con.txt", "acme/1.0/notes/NUL.md", "a/COM1.pdf",
    "LPT9/x.txt", "a/com\u00b9.txt", "a/ CON.txt",
    # control characters
    "a/x\x00.txt", "a/x\ty.txt", "a/x\x1f.txt", "a/x\x7f.txt", "a/x\ny.txt",
])
def test_path_of_refuses_unsafe_paths(docs, source):
    with pytest.raises(UnsafePath):
        docs.path_of(source)


@pytest.mark.parametrize("source", [
    "a/b/c.txt", "a/ leading space.txt", "a/ leading folder/x.txt", "a/b c/x.txt", "a/con-notes.txt",
    "a/console.txt", "a/COM10.txt", "a/Notes (v2).pdf", "documentum/16.2/release-notes/notes.pdf",
])
def test_path_of_accepts_ordinary_names_including_leading_spaces(docs, source):
    assert docs.path_of(source) == docs.documents_dir.joinpath(*source.split("/"))


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


# Links: junctions (Windows, no special rights needed), symlinks (need the right to create them), hard links.

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
    (folder / "secret.txt").write_text("not a document")
    return folder


def files_under(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


@pytest.mark.parametrize("make_link", [make_junction, make_symlink])
@pytest.mark.parametrize("link_at", ["acme", "acme/1.0", "acme/1.0/notes"])
def test_a_link_anywhere_on_the_path_is_refused(docs, outside, make_link, link_at):
    link = docs.documents_dir / link_at
    link.parent.mkdir(parents=True, exist_ok=True)
    make_link(link, outside)

    with pytest.raises(UnsafePath):
        docs.path_of("acme/1.0/notes/new.txt")  # a new file would be written through the link
    with pytest.raises(UnsafePath):
        docs.save(io.BytesIO(b"text"), "acme/1.0/notes/new.txt")
    assert files_under(outside) == ["secret.txt"]  # nothing written outside
    assert temp_files(docs) == []


def test_reaching_an_existing_file_through_a_link_is_refused(docs, outside):
    make_junction(docs.documents_dir / "acme", outside)
    with pytest.raises(UnsafePath):
        docs.path_of("acme/secret.txt")
    with pytest.raises(UnsafePath):
        docs.delete("acme/secret.txt")
    with pytest.raises(UnsafePath):
        docs.readable_file("acme/secret.txt")
    assert (outside / "secret.txt").exists()  # not deleted


def test_a_file_symlink_is_refused(docs, outside):
    (docs.documents_dir / "acme").mkdir()
    make_symlink(docs.documents_dir / "acme" / "notes.txt", outside / "secret.txt")
    with pytest.raises(UnsafePath):
        docs.readable_file("acme/notes.txt")


@pytest.mark.parametrize("make_link", [make_junction, make_symlink])
def test_uploads_are_never_written_through_a_linked_temp_folder(docs, outside, make_link):
    make_link(docs.documents_dir / ".uploads", outside)  # R1
    with pytest.raises(UnsafePath, match="not a plain folder"):
        docs.save(io.BytesIO(b"text"), "acme/1.0/notes/new.txt")
    assert files_under(outside) == ["secret.txt"]
    assert not docs.exists("acme/1.0/notes/new.txt")


def test_a_folder_swapped_for_a_link_during_a_save_is_caught_before_writing(docs, outside, monkeypatch):
    # R2: the first check passes, then the folder becomes a junction before the file is linked into place.
    (docs.documents_dir / "acme").mkdir()
    real_path_of = docs.path_of
    calls = []

    def path_of_then_swap(source):
        calls.append(source)
        path = real_path_of(source)
        if len(calls) == 1:
            (docs.documents_dir / "acme").rmdir()
            make_junction(docs.documents_dir / "acme", outside)
        return path

    monkeypatch.setattr(docs, "path_of", path_of_then_swap)
    with pytest.raises(UnsafePath):
        docs.save(io.BytesIO(b"text"), "acme/1.0/notes/new.txt")
    assert len(calls) == 2  # checked again before any folder was created or anything linked
    assert files_under(outside) == ["secret.txt"]
    assert not any(p.is_dir() for p in outside.iterdir())  # no folders created outside either
    assert temp_files(docs) == []


def test_a_hard_linked_file_is_not_served(docs, outside):
    # R3: its other name may be outside the documents folder.
    (docs.documents_dir / "acme").mkdir()
    os.link(outside / "secret.txt", docs.documents_dir / "acme" / "linked.txt")
    with pytest.raises(UnsafePath, match="2 hard links"):
        docs.readable_file("acme/linked.txt")


def test_readable_file(docs):
    docs.save(io.BytesIO(b"text"), "a/1/notes/x.txt")
    assert docs.readable_file("a/1/notes/x.txt").read_bytes() == b"text"
    assert docs.readable_file("a/1/notes/missing.txt") is None
    (docs.documents_dir / "a" / "1" / "notes" / "folder.txt").mkdir()
    assert docs.readable_file("a/1/notes/folder.txt") is None  # a folder isn't a document
