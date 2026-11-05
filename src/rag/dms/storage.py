"""Original files on disk: where an upload goes in DOCUMENTS_DIR, and saving, finding and deleting it safely.

Files live in the same folder tree the ingestion pipeline reads (<product>/<version>/<doc_type>/<file>), so
uploads are indexed by the existing pipeline and the chat filters work on them unchanged.

An upload is first written to a hidden temporary folder inside DOCUMENTS_DIR (the loaders skip hidden
folders, so ingestion never sees a half-written file), checked there, then linked into place in one step that
fails if a file already exists at that path: an existing document is never overwritten.

Paths are checked by what they contain and for links (symlinks, junctions) on the way; see path_of(). Python
on Windows can't open files relative to an already-checked folder, so a folder swapped for a link between the
check and the write can't be ruled out in code: the checks are repeated just before writing, which narrows
that window, and only the service account and administrators may write to DOCUMENTS_DIR (see the deployment
runbook), which closes it.
"""

import hashlib
import logging
import os
import re
import stat
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from rag.ingestion.loaders import SUPPORTED_SUFFIXES, is_link, load_file

CONTENT_TYPES = {".pdf": "application/pdf", ".md": "text/markdown", ".txt": "text/plain"}
TEMP_FOLDER = ".uploads"  # hidden, so iter_files() skips it
# A temporary upload untouched this long was left by a server that stopped mid-upload (saving writes it
# continuously, so one in progress is always recent).
ABANDONED_AFTER_SECONDS = 60 * 60
MAX_NAME_CHARS = 64  # product, version, doc_type
MAX_FILENAME_CHARS = 120
# The hosting VM doesn't enable long paths, where Windows limits a full path to 259 characters.
MAX_PATH_CHARS = 240
_COPY_CHUNK = 1024 * 1024

logger = logging.getLogger(__name__)

_FOLDER_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9 ._()-]+")
# Names Windows reserves for devices. COM0 and LPT0 are included to be safe; CLOCK$ is no longer one.
_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{port}{n}" for port in ("COM", "LPT") for n in (*"0123456789", "\u00b9", "\u00b2", "\u00b3")}
)
# Control characters (including NUL): never part of a stored file name.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# The names save() gives temporary files: a random 32-digit lowercase hex name and a supported suffix.
_TEMP_NAME = re.compile(r"[0-9a-f]{32}(?:" + "|".join(re.escape(s) for s in sorted(SUPPORTED_SUFFIXES)) + ")")


class InvalidDocument(ValueError):
    """The upload can't be accepted as given; the message is safe to show to the user."""


class DocumentTooLarge(InvalidDocument):
    pass


class UnsafePath(Exception):
    """A path that can't be used safely: outside DOCUMENTS_DIR, through a link, a name Windows treats
    specially, or a file with more than one hard link. Never caused by an upload's own names."""


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
            or _is_device_name(value)):
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
    if _is_device_name(stem):
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
        if len(os.path.abspath(parent)) > MAX_PATH_CHARS:
            raise InvalidDocument("The names are too long for the server's file system; use shorter ones.")
        return "/".join(parts)

    def exists(self, source: str) -> bool:
        return self.path_of(source).exists()

    def path_of(self, source: str) -> Path:
        """The file for a stored source; raises UnsafePath if it would leave DOCUMENTS_DIR.

        The path stays inside because of what it may contain (relative, no "..", no drive, no hidden parts),
        not by comparing resolved paths: under concurrent uploads on Windows, resolving the folder and the
        path gave forms that didn't match, and a valid path was refused. A link (symlink or junction) on the
        way could still lead elsewhere, so existing parts are checked for links; that reads each entry
        itself and is stable.
        """
        parts = source.split("/")
        if (not source or "\\" in source or ":" in source or _CONTROL.search(source)
                or any(not _safe_part(part) for part in parts)):
            raise UnsafePath(f"{source!r} is not a document path inside DOCUMENTS_DIR")
        path = self.documents_dir
        for part in parts:
            path = path / part
            if is_link(path):
                raise UnsafePath(f"{source!r} goes through a link inside DOCUMENTS_DIR")
        return path

    def readable_file(self, source: str) -> Path | None:
        """The existing file for a source, safe to read and serve; None if there is no such file.

        Raises UnsafePath for a file with more than one hard link: another name for it may be outside
        DOCUMENTS_DIR, and nothing this module writes has more than one link once saved.
        """
        path = self.path_of(source)
        try:
            links = path.stat().st_nlink
        except OSError:
            return None
        if not path.is_file():
            return None
        if links > 1:
            raise UnsafePath(f"{source!r} has {links} hard links")
        return path

    def save(self, stream: BinaryIO, source: str) -> SavedFile:
        """Write `stream` to `source`, all or nothing.

        Raises DocumentTooLarge or InvalidDocument (nothing is left behind), or FileExistsError if a file
        already exists there (it is not touched).
        """
        target = self.path_of(source)
        temp = self._temp_dir() / f"{uuid.uuid4().hex}{target.suffix.lower()}"
        try:
            size, digest = self._copy(stream, temp)
            if size == 0:
                raise InvalidDocument("The file is empty.")
            check_content(temp, self.documents_dir)
            # Check again just before creating folders and linking, in case a folder became a link meanwhile.
            self.path_of(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            self.path_of(source)
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

    def _temp_dir(self) -> Path:
        """The hidden folder uploads are written to first; refused if it is a link (it would send them elsewhere)."""
        temp_dir = self.documents_dir / TEMP_FOLDER
        temp_dir.mkdir(exist_ok=True)
        if is_link(temp_dir) or not temp_dir.is_dir():
            raise UnsafePath(f"{temp_dir} is not a plain folder")
        return temp_dir

    def remove_abandoned_uploads(self, older_than: float = ABANDONED_AFTER_SECONDS) -> int:
        """Delete temporary files left in .uploads by a server that stopped mid-upload; returns how many.

        Only what save() creates is touched: regular files directly in a plain .uploads folder, named like its
        temporary files, and untouched for `older_than` seconds, so an upload still being written is left
        alone. Nothing is done through a link: a linked .uploads folder is skipped, and lstat() shows a link
        inside it as a link or a folder, never a regular file. Removing a temporary name never removes a
        document: if the server stopped after linking an upload into place, the document keeps its own name
        (and has one link again, so it can be served). Problems are logged, never raised: this runs when the
        library starts.
        """
        temp_dir = self.documents_dir / TEMP_FOLDER
        try:
            if is_link(temp_dir):
                logger.warning("Not cleaning %s: it is a link", temp_dir)
                return 0
            if not temp_dir.is_dir():
                return 0
            entries = list(temp_dir.iterdir())
        except OSError as error:
            logger.warning("Couldn't check %s for abandoned uploads: %s", temp_dir, error)
            return 0
        cutoff = time.time() - older_than
        removed = 0
        for path in entries:
            try:
                if not _TEMP_NAME.fullmatch(path.name):
                    continue
                info = path.lstat()  # the entry itself: a link is never S_ISREG
                if not stat.S_ISREG(info.st_mode) or info.st_mtime > cutoff:
                    continue
                path.unlink()
                removed += 1
            except OSError as error:
                logger.warning("Couldn't remove the abandoned upload %s: %s", path, error)
        if removed:
            logger.info("Removed %d abandoned upload file(s) from %s", removed, temp_dir)
        return removed

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


def _is_device_name(name: str) -> bool:
    """A name Windows treats as a device. It looks only at the part before the first dot, ignoring spaces
    around it, so "CON", "con.txt", "CON .txt" and " nul..md" all name a device."""
    return name.strip(" ").partition(".")[0].rstrip(" ").upper() in _DEVICE_NAMES


def _safe_part(part: str) -> bool:
    """One folder or file name of a stored path. Leading spaces are allowed (they are real names on Windows);
    a trailing dot or space isn't (Windows drops it, so two names would mean one file), nor a device name."""
    return (part not in ("", ".", "..") and not part.startswith(".") and not part.endswith((".", " "))
            and not _is_device_name(part))


def _existing_name(parent: Path, name: str) -> str:
    """`name`, or the entry in `parent` that differs from it only in capitals."""
    if parent.is_dir():
        wanted = name.casefold()
        for entry in parent.iterdir():
            if entry.name.casefold() == wanted:
                return entry.name
    return name
