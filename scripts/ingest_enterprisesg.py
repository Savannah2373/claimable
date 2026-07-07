#!/usr/bin/env python3
"""Ingest Singapore's flagship business-grant catalog: official Enterprise
Singapore scheme pages, stored as source='enterprisesg' opportunities for the
same compiler and engine.

Like the US benefits vertical, these are curated evergreen scheme pages (no
per-round portal to paginate); the drift monitor re-fetches the same URLs.

Usage:
    python scripts/ingest_enterprisesg.py          # ingest everything in CATALOG
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.db import connect
from claimable.ingestion.benefits import fetch_policy_text, upsert_policy_opportunity

CATALOG = [
    {
        "number": "SG-EDG",
        "title": "Enterprise Development Grant (EDG)",
        "agency": "Enterprise Singapore",
        "url": "https://www.enterprisesg.gov.sg/financial-support/enterprise-development-grant",
    },
    {
        "number": "SG-MRA",
        "title": "Market Readiness Assistance (MRA) Grant",
        "agency": "Enterprise Singapore",
        "url": "https://www.enterprisesg.gov.sg/financial-support/market-readiness-assistance-grant",
    },
    {
        "number": "SG-PSG",
        "title": "Productivity Solutions Grant (PSG)",
        "agency": "Enterprise Singapore",
        "url": "https://www.enterprisesg.gov.sg/financial-support/productivity-solutions-grant",
    },
    # Startup SG Founder is JS-rendered on both enterprisesg.gov.sg and
    # startupsg.gov.sg (plain fetches return an empty shell) — would need a
    # headless browser; documented as future work, not silently skipped.
]

MIN_CHARS = 1500  # a page shorter than this is a redirect/JS shell, not policy


def main() -> None:
    ok, failed = [], []
    with connect() as conn, conn.cursor() as cur:
        for p in CATALOG:
            try:
                text = fetch_policy_text(p["url"])
            except Exception as exc:  # noqa: BLE001 — catalog entries fail independently
                print(f"✗ {p['number']}: fetch failed ({exc})")
                failed.append(p["number"])
                continue
            if len(text) < MIN_CHARS:
                print(f"✗ {p['number']}: page too thin ({len(text)} chars) — likely JS-rendered")
                failed.append(p["number"])
                continue
            upsert_policy_opportunity(
                cur, number=p["number"], title=p["title"],
                agency_name=p["agency"], url=p["url"], policy_text=text,
                source="enterprisesg",
            )
            print(f"✓ {p['number']}: {len(text):,} chars")
            ok.append(p["number"])

    print(f"\nIngested {len(ok)}/{len(CATALOG)}; failed: {failed or 'none'}")
    print("Next: python scripts/embed_opportunities.py && "
          "python scripts/batch_compile.py " + " ".join(ok))


if __name__ == "__main__":
    main()
