"""Load source files from documents/ into LangChain Documents with metadata.

Folder convention: documents/<product>/<version>/<doc_type>/<file>
e.g. documents/documentum/16.2/release-notes/notes.pdf. Levels that are
missing (a file placed higher up) come through as None.
"""

import hashlib
from collections.abc import Iterator
from pathlib import Path

from langchain_core.documents import Document
from pypdf import PdfReader

SUPPORTED_SUFFIXES = {".pdf", ".md", ".txt"}


def iter_files(documents_dir: Path) -> Iterator[Path]:
    """Supported files under documents_dir, in a stable order, skipping hidden ones."""
    for path in sorted(documents_dir.rglob("*")):
        relative = path.relative_to(documents_dir)
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES and not any(
            part.startswith(".") for part in relative.parts
        ):
            yield path


def load_file(path: Path, documents_dir: Path) -> list[Document]:
    """One Document per PDF page with text, or one per Markdown/text file."""
    metadata = file_metadata(path, documents_dir)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        docs = []
        for number, page in enumerate(PdfReader(path).pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:  # pages without a text layer (scans) are skipped
                docs.append(Document(page_content=text, metadata={**metadata, "page": number}))
        return docs
    if suffix in (".md", ".txt"):
        text = path.read_text(encoding="utf-8-sig", errors="replace").strip()
        return [Document(page_content=text, metadata={**metadata, "page": None})] if text else []
    raise ValueError(f"Unsupported file type: {path.name}")


def file_metadata(path: Path, documents_dir: Path) -> dict:
    relative = path.relative_to(documents_dir)
    folders = relative.parts[:-1]
    return {
        "source": relative.as_posix(),
        "file_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        "product": folders[0] if len(folders) > 0 else None,
        "version": folders[1] if len(folders) > 1 else None,
        "doc_type": folders[2] if len(folders) > 2 else None,
    }
