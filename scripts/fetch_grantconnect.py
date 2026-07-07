#!/usr/bin/env python3
"""Pull current grant opportunities from GrantConnect (grants.gov.au, Australia)
and save them as JSONL, ready for scripts/load_opportunities.py.

Usage:
    python scripts/fetch_grantconnect.py --rows 20
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.ingestion.grantconnect import GrantConnectClient
from scripts.fetch_common import write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=20, help="max opportunities to fetch")
    parser.add_argument("--out", default="data/raw", help="output directory")
    args = parser.parse_args()

    client = GrantConnectClient()
    opportunities = [
        client.fetch_detail(uuid, number_hint=number)
        for uuid, number in client.list_current(max_results=args.rows)
    ]
    if len(opportunities) < args.rows:
        print(f"note: portal returned {len(opportunities)} of {args.rows} requested "
              "— fewer current opportunities exist, or the list markup changed")

    out_path = write_jsonl(opportunities, "grantconnect", args.out)
    print(f"\nFetched {len(opportunities)} opportunities → {out_path}\n")
    header = f"{'CLOSES':<12} {'NUMBER':<10} TITLE"
    print(header)
    print("-" * min(len(header) + 60, 100))
    for opp in opportunities:
        title = opp.title if len(opp.title) <= 70 else opp.title[:67] + "..."
        print(f"{opp.close_date or 'TBD':<12} {(opp.number or '?'):<10} {title}")


if __name__ == "__main__":
    main()
