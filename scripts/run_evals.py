#!/usr/bin/env python3
"""The eval harness. Two suites, one exit code — wire this into CI so
regressions block merge.

  1. CRITERIA EXTRACTION — compiled criteria (DB) vs. evals/golden_criteria.json.
     Precision / recall / F1 per grant. Free: no LLM calls.
  2. VERDICTS — runs the live engine for each case in evals/golden_verdicts.json
     and scores verdict accuracy plus MET-precision (the safety metric: of all
     verdicts we called "met", how many were actually met — false "met"s are
     the dangerous error). Costs LLM calls; skip with --skip-verdicts.

Gates: criteria F1 >= 0.75, verdict accuracy >= 0.80, met-precision = 1.00.

Usage:
    python scripts/run_evals.py                  # both suites
    python scripts/run_evals.py --skip-verdicts  # extraction only (free)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.db import connect

EVALS_DIR = Path(__file__).resolve().parents[1] / "evals"

F1_GATE = 0.75
ACCURACY_GATE = 0.80


def eval_criteria(cur) -> tuple[float, float, float]:
    golden_by_grant = json.loads((EVALS_DIR / "golden_criteria.json").read_text())
    golden_by_grant.pop("_comment", None)

    total_tp = total_fp = total_fn = 0
    print("── Suite 1: criteria extraction ─────────────────────────────────────")
    for number, golden in golden_by_grant.items():
        cur.execute(
            """SELECT c.criterion_key, c.text FROM criteria c
               JOIN opportunities o ON o.id = c.opportunity_id
               WHERE o.number = %s AND c.superseded_at IS NULL""",
            (number,),
        )
        predicted = [{"key": r[0], "text": r[1]} for r in cur.fetchall()]
        unused = list(predicted)
        tp = fn = optional_matched = 0

        for item in golden:
            match = next(
                (p for p in unused
                 if p["key"] in item["key_aliases"]
                 or item.get("match_text", "\x00").lower() in p["text"].lower()),
                None,
            )
            if match:
                unused.remove(match)
                if item.get("optional"):
                    optional_matched += 1
                else:
                    tp += 1
            elif not item.get("optional"):
                fn += 1
        fp = len(unused)

        denom_p, denom_r = tp + fp, tp + fn
        p = tp / denom_p if denom_p else 1.0
        r = tp / denom_r if denom_r else 1.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        print(f"  {number:<24} P={p:.2f} R={r:.2f} F1={f1:.2f} "
              f"(tp={tp} fp={fp} fn={fn}{f' +{optional_matched} optional' if optional_matched else ''})")
        for miss in unused:
            print(f"      unexpected: {miss['key']}")
        total_tp, total_fp, total_fn = total_tp + tp, total_fp + fp, total_fn + fn

    p = total_tp / (total_tp + total_fp) if total_tp + total_fp else 1.0
    r = total_tp / (total_tp + total_fn) if total_tp + total_fn else 1.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    print(f"  {'OVERALL':<24} P={p:.2f} R={r:.2f} F1={f1:.2f}\n")
    return p, r, f1


def eval_verdicts(cur) -> tuple[float, float, int]:
    from claimable.engine import analyze  # deferred: needs ANTHROPIC_API_KEY

    cases = json.loads((EVALS_DIR / "golden_verdicts.json").read_text())["cases"]
    correct = total = 0
    met_tp = met_predicted = 0

    print("── Suite 2: verdicts (live engine) ──────────────────────────────────")
    for case in cases:
        cur.execute("SELECT id, kind, name, attrs FROM profiles WHERE name = %s",
                    (case["profile"],))
        prow = cur.fetchone()
        cur.execute("SELECT id FROM opportunities WHERE number = %s", (case["number"],))
        orow = cur.fetchone()
        if not prow or not orow:
            sys.exit(f"Missing profile or opportunity for case: {case['profile']} / {case['number']}")
        profile = {"name": prow[2], "kind": prow[1], "attrs": prow[3]}
        cur.execute(
            """SELECT id, criterion_key, text, check_type, source_quote, threshold
               FROM criteria WHERE opportunity_id = %s AND superseded_at IS NULL""",
            (orow[0],),
        )
        criteria = [
            {"id": r[0], "criterion_key": r[1], "text": r[2], "check_type": r[3],
             "source_quote": r[4], "threshold": r[5]}
            for r in cur.fetchall()
        ]
        result = analyze(profile, criteria)
        got = {v.criterion_key: v.verdict for v in result["verdicts"]}

        print(f"  {case['profile'][:40]:<42} vs {case['number']}")
        for key, expected in case["expected"].items():
            accepted = expected if isinstance(expected, list) else [expected]
            actual = got.get(key, "<criterion missing>")
            ok = actual in accepted
            total += 1
            correct += ok
            if actual == "met":
                met_predicted += 1
                met_tp += "met" in accepted and ok
            mark = "✓" if ok else "✗"
            note = "" if ok else f"  (expected {expected})"
            print(f"    {mark} {key}: {actual}{note}")

    accuracy = correct / total if total else 0.0
    met_precision = met_tp / met_predicted if met_predicted else 1.0
    print(f"\n  verdict accuracy: {correct}/{total} = {accuracy:.2f}")
    print(f"  MET-precision (safety): {met_tp}/{met_predicted} = {met_precision:.2f}\n")
    return accuracy, met_precision, total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-verdicts", action="store_true",
                        help="skip the live-engine suite (no LLM cost)")
    args = parser.parse_args()

    failures = []
    with connect() as conn, conn.cursor() as cur:
        _, _, f1 = eval_criteria(cur)
        if f1 < F1_GATE:
            failures.append(f"criteria F1 {f1:.2f} < gate {F1_GATE}")

        if not args.skip_verdicts:
            accuracy, met_precision, _ = eval_verdicts(cur)
            if accuracy < ACCURACY_GATE:
                failures.append(f"verdict accuracy {accuracy:.2f} < gate {ACCURACY_GATE}")
            if met_precision < 1.0:
                failures.append(f"MET-precision {met_precision:.2f} < 1.00 — false 'met' verdicts!")

    if failures:
        print("EVAL GATE FAILED:")
        for f in failures:
            print(f"  ✗ {f}")
        sys.exit(1)
    print("EVAL GATE PASSED ✅")


if __name__ == "__main__":
    main()
