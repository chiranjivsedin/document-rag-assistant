"""CLI: ask a question and print the generated answer with its sources.

Usage: python scripts/ask.py "question" [--version 16.2] [--product documentum] [--k 5]
Needs Ollama running and the database from DATABASE_URL (docker compose up -d for the local one).
"""

import argparse
import sys
import time

from rag.config import get_settings
from rag.generation.llm import answer
from rag.generation.prompts import NO_ANSWER, source_label
from rag.providers import get_chat_model, get_embeddings, get_vector_store
from rag.retrieval.search import retrieve


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--version", help="only use documents for this version, e.g. 16.2")
    parser.add_argument("--product", help="only use documents for this product")
    parser.add_argument("--k", type=int, default=5, help="number of passages to retrieve (default 5)")
    args = parser.parse_args()

    settings = get_settings()
    started = time.monotonic()
    store = get_vector_store(settings, get_embeddings(settings))
    docs = retrieve(store, args.question, k=args.k, version=args.version, product=args.product)
    result = answer(args.question, docs, get_chat_model(settings))

    print(result.text)
    if result.sources and NO_ANSWER not in result.text:  # don't list passages the model didn't use
        print("\nSources:")
        for number, doc in enumerate(result.sources, 1):
            print(f"  [{number}] {source_label(doc)}  (distance {doc.metadata['distance']:.3f})")
    print(f"\n({time.monotonic() - started:.1f}s, model {settings.llm_model})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
