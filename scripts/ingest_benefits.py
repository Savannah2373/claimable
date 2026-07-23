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

# Major US federal benefit programs. URLs verified fetchable (official .gov +
# USAGov/benefits.gov, which mirror the bot-blocked SSA/Medicaid/FCC pages).
# Bot-blocked/JS-only and thus deferred: NSLP & CSFP (fns.usda.gov now 403s),
# ACP (program ended), Pell (studentaid.gov is JS-rendered).
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
        "number": "TANF-FEDERAL",
        "title": "Temporary Assistance for Needy Families (TANF) — Welfare Benefits",
        "agency": "HHS Administration for Children and Families",
        "url": "https://www.benefits.gov/benefit/613",
    },
    {
        "number": "SSDI-SSI-FEDERAL",
        "title": "SSDI and SSI — Social Security Disability & Supplemental Security Income",
        "agency": "Social Security Administration",
        "url": "https://www.usa.gov/social-security-disability",
    },
    {
        "number": "MEDICAID-CHIP-FEDERAL",
        "title": "Medicaid and CHIP — Health Coverage Eligibility",
        "agency": "Centers for Medicare & Medicaid Services",
        "url": "https://www.benefits.gov/benefit/1637",
    },
    {
        "number": "MEDICARE-FEDERAL",
        "title": "Medicare — Eligibility and How to Apply",
        "agency": "Centers for Medicare & Medicaid Services",
        "url": "https://www.usa.gov/medicare",
    },
    {
        "number": "SECTION8-HCV",
        "title": "Section 8 Housing Choice Voucher Program",
        "agency": "U.S. Department of Housing and Urban Development",
        "url": "https://www.benefits.gov/benefit/710",
    },
    {
        "number": "HEADSTART-FEDERAL",
        "title": "Head Start & Early Head Start — Child Care and Early Education Help",
        "agency": "HHS Administration for Children and Families",
        "url": "https://www.benefits.gov/benefit/616",
    },
    {
        "number": "CTC-IRS",
        "title": "Child Tax Credit — Who Qualifies",
        "agency": "Internal Revenue Service",
        "url": "https://www.irs.gov/credits-deductions/individuals/child-tax-credit",
    },
    {
        "number": "PREMIUM-TAX-CREDIT-IRS",
        "title": "Premium Tax Credit (ACA Marketplace Subsidy) — Eligibility",
        "agency": "Internal Revenue Service",
        "url": "https://www.irs.gov/affordable-care-act/individuals-and-families/eligibility-for-the-premium-tax-credit",
    },
    {
        "number": "WEATHERIZATION-WAP",
        "title": "Weatherization Assistance Program (WAP)",
        "agency": "U.S. Department of Energy",
        "url": "https://www.energy.gov/scep/wap/how-apply-weatherization-assistance",
    },
    {
        "number": "VA-PENSION",
        "title": "VA Pension — Wartime Veterans Needs-Based Benefit",
        "agency": "U.S. Department of Veterans Affairs",
        "url": "https://www.va.gov/pension/eligibility/",
    },
    {
        "number": "UNEMPLOYMENT-FEDERAL",
        "title": "Unemployment Insurance Benefits — Eligibility",
        "agency": "U.S. Department of Labor (state-administered)",
        "url": "https://www.usa.gov/unemployment-benefits",
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
