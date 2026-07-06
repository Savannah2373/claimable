#!/usr/bin/env python3
"""Compile an opportunity's official text into atomic eligibility criteria.

Usage:
    python scripts/compile_criteria.py HRSA-26-050              # compile + store
    python scripts/compile_criteria.py HRSA-26-050 --dry-run    # show source text only

Requires ANTHROPIC_API_KEY in .env (except --dry-run). Only opportunities
fetched with --synopsis have the detail payload the compiler needs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.compiler import build_source_text, compile_criteria, store_criteria, verify_quotes
from claimable.db import connect


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("number", help="opportunity number, e.g. HRSA-26-050")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the assembled source text and exit"
    )
    args = parser.parse_args()

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, number, title, synopsis, raw FROM opportunities WHERE number = %s",
            (args.number,),
        )
        row = cur.fetchone()
        if not row:
            sys.exit(f"Opportunity {args.number} not found. Fetch and load it first.")
        opp_id, number, title, synopsis, raw = row
        opp = {"number": number, "title": title, "synopsis": synopsis, "raw": raw}

        if "detail" not in (raw or {}):
            sys.exit(
                f"{number} has no detail payload. Re-fetch with --synopsis:\n"
                f'  python scripts/fetch_opportunities.py --keyword "..." --synopsis'
            )

        source_text = build_source_text(opp)
        if args.dry_run:
            print(source_text)
            return

        print(f"Compiling criteria for {number}: {title}\n")
        compiled = compile_criteria(source_text)
        checked = verify_quotes(compiled, source_text)

        version = store_criteria(cur, opp_id, compiled)

    verified = sum(1 for _, ok in checked if ok)
    print(f"Stored {len(compiled.criteria)} criteria (version {version}); "
          f"{verified}/{len(checked)} quotes verified against source.\n")
    for c, ok in checked:
        flag = "  " if ok else "⚠ "  # unverified quote — citation not found verbatim
        print(f"{flag}[{c.check_type:13}] {c.criterion_key}: {c.text}")
        print(f'     ↳ "{c.source_quote[:110]}{"..." if len(c.source_quote) > 110 else ""}"')


if __name__ == "__main__":
    main()
