"""Retrieval quality evaluation on the sample contracts (hit-rate@k and MRR).

Usage: python scripts/eval_retrieval.py [--k 5]
Uses the offline hashing embedder unless OPENAI_API_KEY is set, and reports which one was used.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config.settings import Settings  # noqa: E402
from app.agents.llm import build_openai_client  # noqa: E402
from app.rag.embeddings import build_embedder  # noqa: E402
from app.rag.evaluation import evaluate_retrieval  # noqa: E402
from app.rag.retriever import HybridRetriever  # noqa: E402
from app.rag.vector_store import InMemoryVectorStore  # noqa: E402
from tests.helpers import build_stack  # noqa: E402
from tests.test_rag import GOLDEN  # noqa: E402
from app.database.sqlite_store import SqliteTableStore  # noqa: E402
from app.security.auth import LocalAuthService  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()
    settings = Settings(_env_file=str(ROOT / ".env"))
    store = SqliteTableStore(":memory:")
    principal = LocalAuthService(store).ensure_demo_principal()
    tmp = Path(tempfile.mkdtemp())
    stack = build_stack(store, principal, tmp)
    embedder = build_embedder(settings.embedding_provider, build_openai_client(settings), settings.openai_embedding_model)
    vectors = InMemoryVectorStore()
    from app.rag.indexer import DocumentIndexer

    stack.indexer = DocumentIndexer(stack.repos, vectors, embedder)
    stack.ingestion._indexer = stack.indexer  # noqa: SLF001
    for name in ("msa_v1.pdf", "saas_subscription.pdf", "mutual_nda.pdf"):
        stack.ingestion.ingest_file(ROOT / "data" / "samples" / name)
    retriever = HybridRetriever(vectors, embedder, str(principal.org_id))
    result = evaluate_retrieval(retriever, GOLDEN, k=args.k)
    print(f"embedder={embedder.name}  {result.summary()}")
    for row in result.per_case:
        print(f"  rank={row['rank']!s:>4}  {row['query']}")


if __name__ == "__main__":
    main()
