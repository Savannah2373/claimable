#!/usr/bin/env python3
"""Batch-compile rulebooks: enrich missing Grants.gov details, then run the
Criteria Compiler on each opportunity that doesn't have current criteria.

Usage:
    python scripts/batch_compile.py NUM1 NUM2 ...        # specific numbers
    python scripts/batch_compile.py --grants 25          # N soonest-closing
                                                         # uncompiled grants
    python scripts/batch_compile.py --recompile NUM      # force a new version
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.compiler import (
    build_source_text,
    compile_criteria,
    snapshot_source,
    store_criteria,
    verify_quotes,
)
from claimable.db import connect
from claimable.ingestion.grants_gov import GrantsGovClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("numbers", nargs="*", help="opportunity numbers")
    parser.add_argument("--grants", type=int, default=0,
                        help="also take N soonest-closing uncompiled, un-enriched grants")
    parser.add_argument("--recompile", action="store_true",
                        help="compile even if current criteria exist")
    args = parser.parse_args()

    client = GrantsGovClient()
    compiled = skipped = failed = 0

    with connect() as conn, conn.cursor() as cur:
        numbers = list(args.numbers)
        if args.grants:
            cur.execute(
                """SELECT o.number FROM opportunities o
                   WHERE o.source = 'grants.gov' AND o.close_date >= now()::date
                     AND NOT EXISTS (SELECT 1 FROM criteria c
                                     WHERE c.opportunity_id = o.id
                                       AND c.superseded_at IS NULL)
                   ORDER BY o.close_date ASC LIMIT %s""",
                (args.grants,),
            )
            numbers += [r[0] for r in cur.fetchall() if r[0] not in numbers]

        for number in numbers:
            cur.execute(
                "SELECT id, source_id, title, synopsis, raw FROM opportunities WHERE number = %s",
                (number,),
            )
            row = cur.fetchone()
            if not row:
                print(f"?  {number}: not loaded — skipping")
                failed += 1
                continue
            opp_id, source_id, title, synopsis, raw = row
            raw = raw or {}

            if not args.recompile:
                cur.execute(
                    """SELECT 1 FROM criteria WHERE opportunity_id = %s
                       AND superseded_at IS NULL LIMIT 1""",
                    (opp_id,),
                )
                if cur.fetchone():
                    print(f"=  {number}: already compiled — skipping")
                    skipped += 1
                    continue

            if "detail" not in raw and "policy_text" not in raw:
                try:
                    import json as _json

                    detail = client.fetch_detail(source_id)
                    raw = {"search_hit": raw.get("search_hit", raw), "detail": detail}
                    syn = (detail.get("synopsis") or {}).get("synopsisDesc")
                    cur.execute(
                        """UPDATE opportunities SET raw = %s,
                                  synopsis = COALESCE(%s, synopsis), embedding = NULL
                           WHERE id = %s""",
                        (_json.dumps(raw), syn, opp_id),
                    )
                except Exception as exc:  # noqa: BLE001 — one bad fetch shouldn't stop the batch
                    print(f"✗  {number}: detail fetch failed ({exc})")
                    failed += 1
                    continue

            source_text = build_source_text(
                {"number": number, "title": title, "synopsis": synopsis, "raw": raw}
            )
            try:
                result = compile_criteria(source_text)
            except Exception as exc:  # noqa: BLE001
                print(f"✗  {number}: compile failed ({exc})")
                failed += 1
                continue
            checked = verify_quotes(result, source_text)
            version = store_criteria(cur, opp_id, result)
            snapshot_source(cur, opp_id, source_text)
            conn.commit()  # keep progress even if a later item dies
            verified = sum(1 for _, ok in checked if ok)
            flag = "" if verified == len(checked) else f"  ⚠ {len(checked)-verified} unverified quotes"
            print(f"✓  {number}: {len(result.criteria)} criteria v{version} "
                  f"({verified}/{len(checked)} cited){flag}")
            compiled += 1

    print(f"\ncompiled={compiled} skipped={skipped} failed={failed}")
    print("Remember: python scripts/embed_opportunities.py (for newly enriched rows)")


if __name__ == "__main__":
    main()
