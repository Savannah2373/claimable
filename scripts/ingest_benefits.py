#!/usr/bin/env python3
"""Ingest the benefit-program catalog: official eligibility pages for major
individual programs, stored as policy opportunities for the compiler.

Usage:
    python scripts/ingest_benefits.py           # ingest everything in CATALOG
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.db import connect
from claimable.ingestion.benefits import fetch_policy_text, upsert_policy_opportunity

CATALOG = [
    {
        "number": "SNAP-FEDERAL",
        "title": "Supplemental Nutrition Assistance Program (SNAP) — Federal Eligibility",
        "agency": "USDA Food and Nutrition Service",
        "url": "https://www.fns.usda.gov/snap/recipient/eligibility",
    },
    {
        "number": "WIC-FEDERAL",
        "title": "WIC — Special Supplemental Nutrition Program for Women, Infants, and Children",
        "agency": "USDA Food and Nutrition Service",
        "url": "https://www.fns.usda.gov/wic/wic-eligibility-requirements",
    },
    {
        "number": "EITC-FEDERAL",
        "title": "Earned Income Tax Credit (EITC) — Who Qualifies",
        "agency": "Internal Revenue Service",
        "url": "https://www.irs.gov/credits-deductions/individuals/earned-income-tax-credit/who-qualifies-for-the-earned-income-tax-credit-eitc",
    },
    {
        "number": "SSI-FEDERAL",
        "title": "Supplemental Security Income (SSI) — Eligibility",
        "agency": "Social Security Administration",
        "url": "https://www.ssa.gov/ssi/eligibility",
    },
    {
        "number": "MEDICAID-FEDERAL",
        "title": "Medicaid — Who Is Eligible",
        "agency": "U.S. Department of Health and Human Services",
        "url": "https://www.hhs.gov/answers/medicare-and-medicaid/who-is-eligible-for-medicaid/index.html",
    },
    {
        "number": "LIHEAP-FEDERAL",
        "title": "Low Income Home Energy Assistance Program (LIHEAP)",
        "agency": "HHS Administration for Children and Families",
        "url": "https://www.acf.hhs.gov/ocs/low-income-home-energy-assistance-program-liheap",
    },
    {
        "number": "LIFELINE-FCC",
        "title": "Lifeline — Phone and Internet Discount Program",
        "agency": "Federal Communications Commission / USAC",
        "url": "https://www.lifelinesupport.org/do-i-qualify/",
    },
    {
        "number": "NSLP-FEDERAL",
        "title": "National School Lunch Program — Applying for Free and Reduced-Price Meals",
        "agency": "USDA Food and Nutrition Service",
        "url": "https://www.fns.usda.gov/nslp/applying-free-and-reduced-price-school-meals",
    },
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
            )
            print(f"✓ {p['number']}: {len(text):,} chars")
            ok.append(p["number"])

    print(f"\nIngested {len(ok)}/{len(CATALOG)}; failed: {failed or 'none'}")
    print("Next: python scripts/embed_opportunities.py && "
          "python scripts/batch_compile.py " + " ".join(ok))


if __name__ == "__main__":
    main()
