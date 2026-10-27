import hashlib
from pathlib import Path

import pytest

from rag.ingestion.loaders import file_metadata, iter_files, load_file


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
