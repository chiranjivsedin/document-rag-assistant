"""CLI: ingest files from documents/ into the database.

Usage: python scripts/ingest.py [--documents-dir PATH]
Needs Ollama running and the database from DATABASE_URL (docker compose up -d for the local one).
"""

import argparse
import sys
import time
from pathlib import Path

from rag.config import get_settings
from rag.database import indexed_files
from rag.health import check_services
from rag.ingestion.pipeline import ingest
from rag.providers import get_embeddings, get_vector_store


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--documents-dir", type=Path, default=Path(settings.documents_dir))
    args = parser.parse_args()

    if not args.documents_dir.is_dir():
        print(f"Documents folder not found: {args.documents_dir}", file=sys.stderr)
        return 1
    problems = check_services(settings, need_chat_model=False)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1

    started = time.monotonic()
    store = get_vector_store(settings, get_embeddings(settings))
    indexed = indexed_files(settings.database_url, settings.vector_table)
    report = ingest(args.documents_dir, store, indexed, settings)

    for label, sources in [
        ("Added", report.added),
        ("Updated", report.updated),
        ("Removed", report.removed),
        ("No text found (skipped)", report.no_text),
    ]:
        for source in sources:
            print(f"{label}: {source}")
    print(
        f"Done in {time.monotonic() - started:.1f}s: {len(report.added)} added, {len(report.updated)} updated, "
        f"{len(report.unchanged)} unchanged, {len(report.removed)} removed, {report.chunks_stored} chunks stored."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
