#!/usr/bin/env python3
"""Discovery mode: screen one profile against everything relevant.

Usage:
    python scripts/discover.py --profile "Maria R. (synthetic persona)"
    python scripts/discover.py --profile "..." --max-screens 4 --query "rural health"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.db import connect
from claimable.discovery import build_profile_query, discover

BADGES = {"eligible": "🟢 ELIGIBLE  ", "likely": "🟡 LIKELY    ", "not_eligible": "🔴 NOT ELIG. "}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--max-screens", type=int, default=None,
                        help="cap the number of programs screened (default: ALL applicable; "
                             "each screen costs LLM calls)")
    parser.add_argument("--query", help="override the auto-built search query")
    args = parser.parse_args()

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, kind, name, attrs FROM profiles WHERE name = %s",
                    (args.profile,))
        row = cur.fetchone()
        if not row:
            sys.exit(f"Profile {args.profile!r} not found.")
        profile_id, kind, name, attrs = row
        profile = {"name": name, "kind": kind, "attrs": attrs}

        query = args.query or build_profile_query(profile)
        print(f"Discovery for: {name}\nSearch query:  {query!r}\n")
        results = discover(conn, profile_id, profile,
                           max_screens=args.max_screens, query=args.query)

    if not results:
        print("Nothing compiled matched. Compile more rulebooks (scripts/batch_compile.py).")
        return

    print(f"Screened {len(results)} programs:\n")
    for r in results:
        o, c = r["opportunity"], r["counts"]
        print(f"{BADGES[r['status']]} {o['number']:<24} "
              f"{c['met']}✅ {c['not_met']}❌ {c['needs_info']}❓  {o['title'][:56]}")
    open_qs = [q for r in results if r["status"] == "likely" for q in r["open_questions"]]
    if open_qs:
        print("\nAnswer these to firm up the 🟡 results "
              "(scripts/screen.py runs the full loop):")
        for q in dict.fromkeys(open_qs):  # dedupe, keep order
            print(f"  • {q}")


if __name__ == "__main__":
    main()
