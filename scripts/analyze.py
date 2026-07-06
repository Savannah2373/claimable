#!/usr/bin/env python3
"""Run the eligibility engine: one profile vs. one opportunity's criteria.

Usage:
    python scripts/analyze.py --profile "Appalachian Rural Health Data Collaborative" \\
                              --number HRSA-26-050
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.db import connect
from claimable.engine import analyze, store_analysis

ICONS = {"met": "✅ MET      ", "not_met": "❌ NOT MET  ", "needs_info": "❓ NEEDS INFO"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="profile name (see seed_profiles.py)")
    parser.add_argument("--number", required=True, help="opportunity number, e.g. HRSA-26-050")
    args = parser.parse_args()

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, kind, name, attrs FROM profiles WHERE name = %s", (args.profile,))
        prow = cur.fetchone()
        if not prow:
            sys.exit(f"Profile {args.profile!r} not found. Run scripts/seed_profiles.py first.")
        profile_id, kind, name, attrs = prow
        profile = {"name": name, "kind": kind, "attrs": attrs}

        cur.execute("SELECT id, title FROM opportunities WHERE number = %s", (args.number,))
        orow = cur.fetchone()
        if not orow:
            sys.exit(f"Opportunity {args.number} not found.")
        opp_id, title = orow

        cur.execute(
            """SELECT id, criterion_key, text, check_type, source_quote, threshold
               FROM criteria WHERE opportunity_id = %s AND superseded_at IS NULL
               ORDER BY id""",
            (opp_id,),
        )
        criteria = [
            {"id": r[0], "criterion_key": r[1], "text": r[2], "check_type": r[3],
             "source_quote": r[4], "threshold": r[5]}
            for r in cur.fetchall()
        ]
        if not criteria:
            sys.exit(f"No compiled criteria for {args.number}. Run scripts/compile_criteria.py first.")

        print(f"Analyzing: {name}\n      vs.: {title} ({args.number})\n"
              f"           {len(criteria)} criteria · analyst → verifier\n")
        result = analyze(profile, criteria)
        analysis_id = store_analysis(cur, profile_id, opp_id, criteria, result)

    counts = {"met": 0, "not_met": 0, "needs_info": 0}
    print("═" * 78)
    for v in result["verdicts"]:
        counts[v.verdict] += 1
        check = result["checks"].get(v.criterion_key)
        verified = "verified" if (check and check.supported) else "UNVERIFIED"
        print(f"{ICONS[v.verdict]}  {v.criterion_key}  [{verified}]")
        print(f"    {v.reasoning}")
        if v.follow_up_question:
            print(f"    → follow-up: {v.follow_up_question}")
    print("═" * 78)

    if counts["not_met"]:
        headline = "NOT ELIGIBLE as things stand"
    elif counts["needs_info"]:
        headline = "LIKELY ELIGIBLE — pending answers"
    else:
        headline = "APPEARS ELIGIBLE on all published criteria"
    print(f"\n{headline}: {counts['met']} met · {counts['not_met']} not met · "
          f"{counts['needs_info']} needs info   (analysis #{analysis_id} stored)")
    print("Screening only — eligibility is always determined by the issuing agency.")


if __name__ == "__main__":
    main()
