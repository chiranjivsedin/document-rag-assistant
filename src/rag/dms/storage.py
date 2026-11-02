"""Original files on disk: where an upload goes in DOCUMENTS_DIR, and saving, finding and deleting it safely.

Files live in the same folder tree the ingestion pipeline reads (<product>/<version>/<doc_type>/<file>), so
uploads are indexed by the existing pipeline and the chat filters work on them unchanged.

An upload is first written to a hidden temporary folder inside DOCUMENTS_DIR (the loaders skip hidden
folders, so ingestion never sees a half-written file), checked there, then linked into place in one step that
fails if a file already exists at that path: an existing document is never overwritten.
"""

import hashlib
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from rag.ingestion.loaders import SUPPORTED_SUFFIXES, load_file

CONTENT_TYPES = {".pdf": "application/pdf", ".md": "text/markdown", ".txt": "text/plain"}
TEMP_FOLDER = ".uploads"  # hidden, so iter_files() skips it
MAX_NAME_CHARS = 64  # product, version, doc_type
MAX_FILENAME_CHARS = 120
# The hosting VM doesn't enable long paths, where Windows limits a full path to 259 characters.
MAX_PATH_CHARS = 240
_COPY_CHUNK = 1024 * 1024

_FOLDER_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9 ._()-]+")
# Names Windows reserves for devices, with or without an extension (CON, CON.pdf, ...).
_RESERVED = re.compile(r"(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..*)?", re.IGNORECASE)


class InvalidDocument(ValueError):
    """The upload can't be accepted as given; the message is safe to show to the user."""


class DocumentTooLarge(InvalidDocument):
    pass


class UnsafePath(Exception):
    """A stored source doesn't resolve to a file inside DOCUMENTS_DIR (it was not written by this module)."""


@dataclass(frozen=True)
class SavedFile:
    source: str  # path relative to DOCUMENTS_DIR, forward slashes
    size_bytes: int
    file_hash: str  # SHA-256, lowercase hex


def check_folder_name(value: str, field: str) -> str:
    """A product, version or doc_type value: one safe folder name, returned trimmed."""
    value = value.strip()
    if not value:
        raise InvalidDocument(f"{field} is required.")
    if (len(value) > MAX_NAME_CHARS or not _FOLDER_NAME.fullmatch(value) or ".." in value
            or _RESERVED.fullmatch(value)):
        raise InvalidDocument(
            f"{field} must be 1-{MAX_NAME_CHARS} letters, digits, dots, hyphens or underscores, "
            "starting and ending with a letter or digit (for example 16.2 or release-notes)."
        )
    return value


def clean_filename(original: str) -> str:
    """The name to store an upload under: the original's last path part, reduced to safe characters.

    Raises InvalidDocument if its type isn't supported or nothing usable is left.
    """
    name = unicodedata.normalize("NFKD", original.replace("\\", "/").rsplit("/", 1)[-1])
    name = name.encode("ascii", "ignore").decode()  # é -> e; characters with no ASCII form are dropped
    stem, dot, suffix = name.rpartition(".")
    suffix = f".{suffix.lower()}" if dot else ""
    if suffix not in SUPPORTED_SUFFIXES:
        allowed = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise InvalidDocument(f"Only {allowed} files can be uploaded.")
    stem = _UNSAFE_FILENAME_CHARS.sub("_", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .")  # Windows drops trailing dots and spaces
    stem = stem[:MAX_FILENAME_CHARS - len(suffix)].rstrip(" .") or "document"
    if _RESERVED.fullmatch(stem):
        stem = f"_{stem}"
    return stem + suffix


def check_content(path: Path, documents_dir: Path) -> None:
    """Reject a saved upload that isn't what its extension says, or that ingestion couldn't read."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        with path.open("rb") as file:
            if file.read(5) != b"%PDF-":
                raise InvalidDocument("This file is not a PDF.")
        try:
            load_file(path, documents_dir)  # exactly what ingestion will do with it
        except Exception:
            raise InvalidDocument("This PDF can't be read; it may be damaged or password-protected.") from None
        return
    data = path.read_bytes()
    if b"\x00" in data:
        raise InvalidDocument("This file is not plain text.")
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InvalidDocument("Text and Markdown files must be saved as UTF-8.") from None


class DocumentStorage:
    """Original files under DOCUMENTS_DIR. Every path is derived from checked names and confirmed to stay inside."""

    def __init__(self, documents_dir: Path, max_bytes: int):
        self.documents_dir = documents_dir
        self.max_bytes = max_bytes

    def source_for(self, product: str, version: str, doc_type: str, filename: str) -> str:
        """Where an upload with these (already checked) names goes, relative to DOCUMENTS_DIR.

        Folder and file names that already exist with different capitals are reused as they are: Windows
        treats them as the same, and the stored source must match the path ingestion reads.
        """
        parts, parent = [], self.documents_dir
        for name in (product, version, doc_type, filename):
            name = _existing_name(parent, name)
            parts.append(name)
            parent = parent / name
        if len(str(parent.resolve())) > MAX_PATH_CHARS:
            raise InvalidDocument("The names are too long for the server's file system; use shorter ones.")
        return "/".join(parts)

    def exists(self, source: str) -> bool:
        return self.path_of(source).exists()

    def path_of(self, source: str) -> Path:
        """The file for a stored source; raises UnsafePath if it would leave DOCUMENTS_DIR."""
        root = self.documents_dir.resolve()
        path = (root / source).resolve()
        if path == root or not path.is_relative_to(root) or any(p.startswith(".") for p in Path(source).parts):
            raise UnsafePath(f"{source!r} is not a document path inside DOCUMENTS_DIR")
        return path

    def save(self, stream: BinaryIO, source: str) -> SavedFile:
        """Write `stream` to `source`, all or nothing.

        Raises DocumentTooLarge or InvalidDocument (nothing is left behind), or FileExistsError if a file
        already exists there (it is not touched).
        """
        target = self.path_of(source)
        temp_dir = self.documents_dir / TEMP_FOLDER
        temp_dir.mkdir(exist_ok=True)
        temp = temp_dir / f"{uuid.uuid4().hex}{target.suffix.lower()}"
        try:
            size, digest = self._copy(stream, temp)
            if size == 0:
                raise InvalidDocument("The file is empty.")
            check_content(temp, self.documents_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(temp, target)  # atomic, and fails if the target exists (unlike a rename on Linux)
        finally:
            temp.unlink(missing_ok=True)
        return SavedFile(source=source, size_bytes=size, file_hash=digest)

    def delete(self, source: str) -> bool:
        """Delete the file; False if it was already gone. Folders are left in place."""
        try:
            self.path_of(source).unlink()
        except FileNotFoundError:
            return False
        return True

    def _copy(self, stream: BinaryIO, temp: Path) -> tuple[int, str]:
        size, digest = 0, hashlib.sha256()
        with temp.open("xb") as out:
            while chunk := stream.read(_COPY_CHUNK):
                size += len(chunk)
                if size > self.max_bytes:
                    raise DocumentTooLarge(f"The file is larger than the {self.max_bytes // (1024 * 1024)} MB limit.")
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        return size, digest.hexdigest()


def _existing_name(parent: Path, name: str) -> str:
    """`name`, or the entry in `parent` that differs from it only in capitals."""
    if parent.is_dir():
        wanted = name.casefold()
        for entry in parent.iterdir():
            if entry.name.casefold() == wanted:
                return entry.name
    return name
