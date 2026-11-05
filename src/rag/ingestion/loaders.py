"""Load source files from documents/ into LangChain Documents with metadata.

Folder convention: documents/<product>/<version>/<doc_type>/<file>
e.g. documents/documentum/16.2/release-notes/notes.pdf. Levels that are
missing (a file placed higher up) come through as None.
"""

import hashlib
import logging
import os
import stat
from collections.abc import Iterator
from pathlib import Path

from langchain_core.documents import Document
from pypdf import PdfReader

SUPPORTED_SUFFIXES = {".pdf", ".md", ".txt"}

logger = logging.getLogger(__name__)


class DocumentsFolderUnreadable(RuntimeError):
    """The documents folder itself is missing or can't be listed.

    Raised instead of listing nothing, which would make ingestion remove everything it had indexed. Not an
    OSError on purpose: the API reports OSErrors as Ollama or the database being unreachable.
    """


def is_link(path: Path) -> bool:
    """A symlink, or a Windows junction (a directory "mount point" reparse point).

    Path.is_junction exists only from Python 3.12; before that, lstat's reparse tag tells the same (it is what
    Python 3.12 checks itself). A missing path is not a link.
    """
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None:
        return is_junction()
    try:
        return getattr(os.lstat(path), "st_reparse_tag", 0) == stat.IO_REPARSE_TAG_MOUNT_POINT
    except (OSError, ValueError):
        return False


def iter_files(documents_dir: Path) -> Iterator[Path]:
    """Supported files under documents_dir, in a stable order, skipping hidden ones.

    Never goes through a link (a symlink or a junction, to a folder or a file) and skips files with more than
    one hard link: either could bring in a file from outside documents_dir. Each one skipped is logged, as is a
    subfolder that can't be read. A file that is skipped counts as gone, so ingestion removes whatever it had
    indexed from it.

    Raises DocumentsFolderUnreadable if documents_dir itself is missing or can't be listed, before anything is
    returned, so a misconfigured or inaccessible folder can never empty the index.
    """
    # documents_dir itself is configuration and is used wherever it points; only links inside it are refused.
    if not documents_dir.is_dir():
        raise DocumentsFolderUnreadable(f"The documents folder {documents_dir} doesn't exist or isn't a folder")
    found: list[Path] = []
    folders = [documents_dir]
    while folders:
        folder = folders.pop()
        try:
            entries = list(folder.iterdir())
        except OSError as error:
            if folder is documents_dir:
                raise DocumentsFolderUnreadable(
                    f"The documents folder {documents_dir} can't be read: {error}") from error
            logger.warning("Not indexing %s: the folder can't be read (%s)", folder, error)
            continue
        for path in entries:
            if path.name.startswith("."):
                continue  # hidden, like the upload library's temporary folder
            try:
                if is_link(path):
                    logger.warning("Not indexing %s: it is a link, which may lead outside the documents folder", path)
                elif path.is_dir():
                    folders.append(path)
                elif path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
                    links = path.stat().st_nlink
                    if links > 1:
                        logger.warning("Not indexing %s: it has %d hard links, so the same file may also be "
                                       "outside the documents folder", path, links)
                    else:
                        found.append(path)
            except OSError as error:
                logger.warning("Not indexing %s: %s", path, error)
    yield from sorted(found)


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
