#!/usr/bin/env python3
"""Pull live grant opportunities from Grants.gov and save them as JSONL.

Usage:
    python scripts/fetch_opportunities.py --keyword "youth education" --rows 25
    python scripts/fetch_opportunities.py --keyword "rural health" --rows 10 --synopsis
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.ingestion.grants_gov import GrantsGovClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keyword", default="", help="search keyword(s)")
    parser.add_argument("--rows", type=int, default=25, help="max opportunities to fetch")
    parser.add_argument(
        "--statuses", default="posted", help="pipe-separated: posted|forecasted|closed"
    )
    parser.add_argument(
        "--synopsis",
        action="store_true",
        help="also fetch full synopsis per opportunity (one extra API call each)",
    )
    parser.add_argument("--out", default="data/raw", help="output directory")
    args = parser.parse_args()

    client = GrantsGovClient()
    opportunities = list(
        client.search(keyword=args.keyword, statuses=args.statuses, max_results=args.rows)
    )

    if args.synopsis:
        for opp in opportunities:
            client.enrich_with_synopsis(opp)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = args.keyword.replace(" ", "-") or "all"
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"grants_gov_{slug}_{stamp}.jsonl"

    with out_path.open("w") as f:
        for opp in opportunities:
            f.write(json.dumps(dataclasses.asdict(opp)) + "\n")

    print(f"\nFetched {len(opportunities)} opportunities → {out_path}\n")
    header = f"{'CLOSES':<12} {'NUMBER':<28} {'AGENCY':<12} TITLE"
    print(header)
    print("-" * min(len(header) + 40, 100))
    for opp in opportunities:
        title = opp.title if len(opp.title) <= 60 else opp.title[:57] + "..."
        print(
            f"{opp.close_date or 'TBD':<12} {(opp.number or '?')[:27]:<28} "
            f"{(opp.agency_code or '?')[:11]:<12} {title}"
        )


if __name__ == "__main__":
    main()
