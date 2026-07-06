"""USAspending.gov enrichment — who actually wins awards under this
assistance listing, and at what size. Free API, no key.

Grounds the planner's expectations in reality: a program whose recent awards
all cluster near the ceiling tells an applicant something the NOFO doesn't.
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import median
from typing import Any

import requests

_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
_GRANT_TYPE_CODES = ["02", "03", "04", "05"]  # block grant..cooperative agreement


def award_stats(aln: str, lookback_days: int = 730, sample_limit: int = 100) -> dict[str, Any] | None:
    """Recent-award stats for one Assistance Listing Number (e.g. '93.155').
    Returns None when the API yields nothing usable."""
    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    body = {
        "filters": {
            "award_type_codes": _GRANT_TYPE_CODES,
            "program_numbers": [aln],
            "time_period": [{"start_date": start, "end_date": date.today().isoformat()}],
        },
        "fields": ["Award ID", "Recipient Name", "Award Amount", "Start Date"],
        "limit": sample_limit,
        "page": 1,
        "sort": "Award Amount",
        "order": "desc",
    }
    try:
        resp = requests.post(_URL, json=body, timeout=30)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except requests.RequestException:
        return None
    amounts = [r["Award Amount"] for r in results if r.get("Award Amount")]
    if not amounts:
        return None
    return {
        "aln": aln,
        "sample_size": len(amounts),
        "max_usd": max(amounts),
        "min_usd": min(amounts),
        "median_usd": median(amounts),
        "recent_recipients": [r["Recipient Name"] for r in results[:5]],
        "note": f"sample of up to {sample_limit} awards since {start}, sorted by amount",
    }


def alns_for_opportunity(raw: dict[str, Any]) -> list[str]:
    """Pull Assistance Listing (CFDA) numbers from a stored opportunity's raw
    payload — detail.cfdas[].cfdaNumber, falling back to the search hit's
    cfdaList."""
    detail_cfdas = (raw.get("detail") or {}).get("cfdas") or []
    numbers = [c.get("cfdaNumber") for c in detail_cfdas if c.get("cfdaNumber")]
    if numbers:
        return numbers
    hit = raw.get("search_hit", raw)
    if isinstance(hit, dict) and "search_hit" in hit:
        hit = hit["search_hit"]
    return [n for n in (hit.get("cfdaList") or []) if n]
