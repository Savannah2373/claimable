#!/usr/bin/env python3
"""Hybrid search over ingested opportunities.

Usage:
    python scripts/search.py "small youth education nonprofit in Ohio"
    python scripts/search.py --k 15 "rural hospital broadband"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.db import connect
from claimable.search import hybrid_search


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="plain-English description of who you are / what you need")
    parser.add_argument("--k", type=int, default=10, help="number of results")
    parser.add_argument("--no-rerank", action="store_true", help="skip the cross-encoder rerank")
    args = parser.parse_args()

    with connect() as conn:
        hits = hybrid_search(conn, args.query, k=args.k, rerank=not args.no_rerank)

    if not hits:
        print("No matches. Ingest more opportunities or broaden the query.")
        return

    print(f"\nTop {len(hits)} matches for: {args.query!r}"
          f"{' (reranked)' if not args.no_rerank else ''}\n")
    print(f"{'RERANK':<8} {'RRF':<8} {'VIA':<8} {'CLOSES':<12} {'NUMBER':<24} TITLE")
    print("-" * 100)
    for h in hits:
        title = h.title if len(h.title) <= 42 else h.title[:39] + "..."
        rr = f"{h.rerank_score:.2f}" if h.rerank_score is not None else "—"
        print(
            f"{rr:<8} {h.score:<8.4f} {h.matched_by:<8} {h.close_date or 'TBD':<12} "
            f"{(h.number or '?')[:23]:<24} {title}"
        )


if __name__ == "__main__":
    main()
