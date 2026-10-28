"""CLI: ask a question and print the generated answer with its sources.

Usage: python scripts/ask.py "question" [--version 16.2] [--product documentum] [--k 5]
Needs Ollama running and the database from DATABASE_URL (docker compose up -d for the local one).
"""

import argparse
import sys
import time

from rag.config import get_settings
from rag.generation.llm import stream_answer
from rag.generation.prompts import source_label
from rag.health import check_services
from rag.retrieval.search import retrieve
from rag.service import build_services, sources_to_show


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--version", help="only use documents for this version, e.g. 16.2")
    parser.add_argument("--product", help="only use documents for this product")
    parser.add_argument("--k", type=int, default=5, help="number of passages to retrieve (default 5)")
    args = parser.parse_args()

    settings = get_settings()
    problems = check_services(settings, need_chat_model=True)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1

    started = time.monotonic()
    services = build_services(settings)
    docs = retrieve(services.store, args.question, k=args.k, version=args.version, product=args.product)
    text = ""
    for piece in stream_answer(args.question, docs, services.chat_model):
        print(piece, end="", flush=True)  # show the answer as the model writes it
        text += piece
    print()

    sources = sources_to_show(text, docs)
    if sources:
        print("\nSources:")
        for number, doc in enumerate(sources, 1):
            print(f"  [{number}] {source_label(doc)}  (distance {doc.metadata['distance']:.3f})")
    print(f"\n({time.monotonic() - started:.1f}s, model {settings.llm_model})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
