#!/usr/bin/env python3
"""Fetch the full Grants.gov detail payload (synopsis, eligibility text) for
already-loaded opportunities that only have a search hit.

Usage:
    python scripts/enrich_details.py TI-26-017 L26AS00064 USDA-NIFA-AFRI-011596
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.db import connect
from claimable.ingestion.grants_gov import GrantsGovClient


def main() -> None:
    numbers = sys.argv[1:]
    if not numbers:
        sys.exit(__doc__)

    client = GrantsGovClient()
    with connect() as conn, conn.cursor() as cur:
        for number in numbers:
            cur.execute(
                "SELECT id, source_id, raw FROM opportunities WHERE number = %s", (number,)
            )
            row = cur.fetchone()
            if not row:
                print(f"skip (not loaded): {number}")
                continue
            opp_id, source_id, raw = row
            if "detail" in (raw or {}):
                print(f"already enriched:  {number}")
                continue
            detail = client.fetch_detail(source_id)
            synopsis = (detail.get("synopsis") or {}).get("synopsisDesc")
            cur.execute(
                """UPDATE opportunities
                   SET raw = %s, synopsis = COALESCE(%s, synopsis), embedding = NULL
                   WHERE id = %s""",
                (json.dumps({"search_hit": raw, "detail": detail}), synopsis, opp_id),
            )
            print(f"enriched:          {number}")
    print("\nRe-run scripts/embed_opportunities.py to refresh embeddings for enriched rows.")


if __name__ == "__main__":
    main()
