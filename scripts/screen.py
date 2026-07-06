#!/usr/bin/env python3
"""The full screening loop: analyze → answer NEEDS INFO follow-ups → intake
agent structures the answers → re-analyze → action plan report.

Usage:
    # interactive: you type answers to follow-up questions
    python scripts/screen.py --profile "Appalachian Rural Health Data Collaborative" \\
                             --number HRSA-26-050

    # non-interactive: answers keyed by criterion_key in a JSON file
    python scripts/screen.py --profile "..." --number HRSA-26-050 \\
                             --answers answers.json

Intake updates are session-local by default (goldens stay reproducible);
pass --save to persist learned facts back to the profile row.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.db import connect
from claimable.engine import analyze, store_analysis
from claimable.enrichment.usaspending import alns_for_opportunity, award_stats
from claimable.intake import structure_answers
from claimable.planner import build_plan, render_markdown

ICONS = {"met": "✅ MET      ", "not_met": "❌ NOT MET  ", "needs_info": "❓ NEEDS INFO"}
REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"


def print_matrix(result) -> dict[str, int]:
    counts = {"met": 0, "not_met": 0, "needs_info": 0}
    print("═" * 78)
    for v in result["verdicts"]:
        counts[v.verdict] += 1
        check = result["checks"].get(v.criterion_key)
        verified = "verified" if (check and check.supported) else "UNVERIFIED"
        print(f"{ICONS[v.verdict]}  {v.criterion_key}  [{verified}]")
        print(f"    {v.reasoning}")
        if v.follow_up_question:
            print(f"    → {v.follow_up_question}")
    print("═" * 78)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--number", required=True)
    parser.add_argument("--answers", help="JSON file: {criterion_key: answer text}")
    parser.add_argument("--rounds", type=int, default=2, help="max analyze rounds")
    parser.add_argument("--save", action="store_true",
                        help="persist intake-learned facts to the profile row")
    parser.add_argument("--no-plan", action="store_true", help="skip the planner report")
    args = parser.parse_args()

    canned = json.loads(Path(args.answers).read_text()) if args.answers else None

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, kind, name, attrs FROM profiles WHERE name = %s", (args.profile,))
        prow = cur.fetchone()
        if not prow:
            sys.exit(f"Profile {args.profile!r} not found.")
        profile_id, kind, name, attrs = prow
        profile = {"name": name, "kind": kind, "attrs": dict(attrs)}

        cur.execute(
            "SELECT id, number, title, agency_name, close_date::text FROM opportunities WHERE number = %s",
            (args.number,),
        )
        orow = cur.fetchone()
        if not orow:
            sys.exit(f"Opportunity {args.number} not found.")
        opp_id, number, title, agency, close_date = orow
        opportunity = {"number": number, "title": title, "agency": agency, "close_date": close_date}

        cur.execute(
            """SELECT id, criterion_key, text, check_type, source_quote, threshold
               FROM criteria WHERE opportunity_id = %s AND superseded_at IS NULL ORDER BY id""",
            (opp_id,),
        )
        criteria = [
            {"id": r[0], "criterion_key": r[1], "text": r[2], "check_type": r[3],
             "source_quote": r[4], "threshold": r[5]}
            for r in cur.fetchall()
        ]
        if not criteria:
            sys.exit(f"No compiled criteria for {number}.")

        result = None
        for round_no in range(1, args.rounds + 1):
            print(f"\n─ Round {round_no}: {name} vs {number} "
                  f"({len(criteria)} criteria) ─")
            result = analyze(profile, criteria)
            store_analysis(cur, profile_id, opp_id, criteria, result)
            counts = print_matrix(result)

            needs = [v for v in result["verdicts"] if v.verdict == "needs_info"]
            if not needs or round_no == args.rounds:
                break

            qa_pairs = []
            for v in needs:
                question = v.follow_up_question or f"Please clarify: {v.reasoning}"
                if canned is not None:
                    answer = canned.get(v.criterion_key, "")
                else:
                    print(f"\n{question}")
                    answer = input("> ").strip()
                if answer:
                    qa_pairs.append({"question": question, "answer": answer})
            if not qa_pairs:
                break

            print(f"\n… intake agent structuring {len(qa_pairs)} answer(s)")
            new_facts = structure_answers(qa_pairs, known_fact_keys=list(profile["attrs"]))
            if not new_facts:
                print("  no usable facts extracted; stopping.")
                break
            for k, val in new_facts.items():
                print(f"  learned: {k} = {val!r}")
            profile["attrs"].update(new_facts)
            if args.save:
                cur.execute("UPDATE profiles SET attrs = %s WHERE id = %s",
                            (json.dumps(profile["attrs"]), profile_id))

        headline_counts = counts
        if headline_counts["not_met"]:
            print(f"\nNOT ELIGIBLE as things stand.")
        elif headline_counts["needs_info"]:
            print(f"\nLIKELY ELIGIBLE — pending answers.")
        else:
            print(f"\nAPPEARS ELIGIBLE on all published criteria.")

        if args.no_plan:
            return

        print("\n… planner agent drafting the action plan")
        cur.execute("SELECT raw FROM opportunities WHERE id = %s", (opp_id,))
        raw = cur.fetchone()[0]
        alns = alns_for_opportunity(raw)
        stats = award_stats(alns[0]) if alns else None
        verdict_dicts = [
            {"criterion_key": v.criterion_key, "verdict": v.verdict,
             "reasoning": v.reasoning, "follow_up_question": v.follow_up_question}
            for v in result["verdicts"]
        ]
        plan = build_plan(profile, opportunity, verdict_dicts, stats)
        report = render_markdown(plan, profile, opportunity, verdict_dicts)

        REPORTS_DIR.mkdir(exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", f"{number}-{name}".lower()).strip("-")
        out = REPORTS_DIR / f"{slug}.md"
        out.write_text(report)
        print(f"\n{plan.headline}")
        print(f"Report: {out.relative_to(Path.cwd()) if out.is_relative_to(Path.cwd()) else out}")


if __name__ == "__main__":
    main()
