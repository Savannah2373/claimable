#!/usr/bin/env python3
"""Ingest the USDA FNS SNAP eligibility page as a policy opportunity —
the benefits vertical's first rulebook.

Usage:
    python scripts/ingest_snap.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.db import connect
from claimable.ingestion.benefits import fetch_policy_text, upsert_policy_opportunity

URL = "https://www.fns.usda.gov/snap/recipient/eligibility"


def main() -> None:
    print(f"Fetching {URL}")
    text = fetch_policy_text(URL)
    print(f"Extracted {len(text):,} characters of policy text")

    with connect() as conn, conn.cursor() as cur:
        opp_id = upsert_policy_opportunity(
            cur,
            number="SNAP-FEDERAL",
            title="Supplemental Nutrition Assistance Program (SNAP) — Federal Eligibility",
            agency_name="USDA Food and Nutrition Service",
            url=URL,
            policy_text=text,
        )
    print(f"Stored as opportunity #{opp_id} (number SNAP-FEDERAL, source 'policy')")
    print("Next: python scripts/compile_criteria.py SNAP-FEDERAL")


if __name__ == "__main__":
    main()
