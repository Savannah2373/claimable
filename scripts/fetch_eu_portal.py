#!/usr/bin/env python3
"""Pull open grant topics from the EU Funding & Tenders Portal (SEDIA) and
save them as JSONL, ready for scripts/load_opportunities.py.

The search facet over-reports "open", so more identifiers are scanned than
kept: only topics whose detail page confirms Open status are written.

Usage:
    python scripts/fetch_eu_portal.py --rows 15
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.ingestion.eu_portal import EUPortalClient
from scripts.fetch_common import write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=15,
                        help="max confirmed-open topics to fetch")
    parser.add_argument("--scan", type=int, default=120,
                        help="max search identifiers to scan for open topics")
    parser.add_argument("--out", default="data/raw", help="output directory")
    args = parser.parse_args()

    client = EUPortalClient()
    opportunities = []
    scanned = 0
    for ident in client.list_open_topics(max_results=args.scan):
        scanned += 1
        opp = client.fetch_detail(ident)
        if opp is not None:
            opportunities.append(opp)
            if len(opportunities) >= args.rows:
                break
    if len(opportunities) < args.rows:
        print(f"note: {len(opportunities)} of {args.rows} requested after "
              f"scanning {scanned} identifiers — raise --scan for more")

    out_path = write_jsonl(opportunities, "eu_portal", args.out)
    print(f"\nFetched {len(opportunities)} open topics → {out_path}\n")
    header = f"{'CLOSES':<12} {'PROGRAMME':<10} IDENTIFIER / TITLE"
    print(header)
    print("-" * min(len(header) + 60, 100))
    for opp in opportunities:
        title = opp.title if len(opp.title) <= 56 else opp.title[:53] + "..."
        print(f"{opp.close_date or 'TBD':<12} {(opp.agency_code or '?'):<10} "
              f"{opp.number} — {title}")


if __name__ == "__main__":
    main()
