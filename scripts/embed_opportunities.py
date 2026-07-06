#!/usr/bin/env python3
"""Embed opportunities that don't have a vector yet (title + synopsis).

Usage:
    python scripts/embed_opportunities.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.db import connect
from claimable.embeddings import embed_documents

_BATCH = 32


def main() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, title || E'\\n' || coalesce(synopsis, '') AS text
               FROM opportunities WHERE embedding IS NULL ORDER BY id"""
        )
        rows = cur.fetchall()
        if not rows:
            print("Nothing to embed — all opportunities have vectors.")
            return

        done = 0
        for i in range(0, len(rows), _BATCH):
            batch = rows[i : i + _BATCH]
            vectors = embed_documents([text for _, text in batch])
            for (opp_id, _), vec in zip(batch, vectors):
                cur.execute(
                    "UPDATE opportunities SET embedding = %s::vector WHERE id = %s",
                    (vec, opp_id),
                )
            done += len(batch)
            print(f"  embedded {done}/{len(rows)}")

    print(f"Done: {len(rows)} opportunities embedded.")


if __name__ == "__main__":
    main()
