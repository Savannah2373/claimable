"""Discovery mode — "screen me against everything relevant."

Builds a search query from the profile itself, retrieves the most relevant
compiled rulebooks (hybrid + rerank), runs the full eligibility engine on each,
and returns a ranked summary: eligible → likely (open questions) → not eligible.

For individuals, every compiled benefit program is always included — benefit
programs are few and high-value, so they don't have to win a search race.
"""

from __future__ import annotations

from typing import Any

import psycopg

from claimable.engine import analyze, store_analysis
from claimable.search import hybrid_search

STATUS_ORDER = {"eligible": 0, "likely": 1, "not_eligible": 2}


def build_profile_query(profile: dict[str, Any]) -> str:
    a = profile.get("attrs", {})
    parts: list[str] = []
    if profile.get("kind") == "organization":
        parts += [str(a.get("entity_type", "")), str(a.get("mission", ""))]
    else:
        parts.append("individual person household benefits assistance")
        if a.get("household_size"):
            parts.append(f"household of {a['household_size']}")
        if a.get("employment"):
            parts.append(str(a["employment"]))
        if a.get("monthly_gross_income_usd"):
            parts.append("low income")
    if a.get("state"):
        parts.append(str(a["state"]))
    return " ".join(p for p in parts if p).strip()


def _load_criteria(cur, opp_id: int) -> list[dict[str, Any]]:
    cur.execute(
        """SELECT id, criterion_key, text, check_type, source_quote, threshold
           FROM criteria WHERE opportunity_id = %s AND superseded_at IS NULL ORDER BY id""",
        (opp_id,),
    )
    return [{"id": r[0], "criterion_key": r[1], "text": r[2], "check_type": r[3],
             "source_quote": r[4], "threshold": r[5]} for r in cur.fetchall()]


def discover(
    conn: psycopg.Connection,
    profile_id: int,
    profile: dict[str, Any],
    max_screens: int = 5,
    query: str | None = None,
) -> list[dict[str, Any]]:
    """Search → pick top compiled matches → screen each → ranked summaries."""
    query = query or build_profile_query(profile)

    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT o.id, o.number, o.title, o.source, o.close_date::text
               FROM opportunities o
               JOIN criteria c ON c.opportunity_id = o.id AND c.superseded_at IS NULL"""
        )
        compiled = {r[1]: {"id": r[0], "number": r[1], "title": r[2],
                           "source": r[3], "close_date": r[4]} for r in cur.fetchall()}

    hits = hybrid_search(conn, query, k=max(20, max_screens * 3))
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    if profile.get("kind") == "individual":
        # benefit programs first for people — they're few, high-value, and
        # shouldn't lose their screening slot to a search-ranked grant
        for opp in compiled.values():
            if opp["source"] == "policy" and opp["number"] not in seen:
                targets.append(opp)
                seen.add(opp["number"])
    for h in hits:
        if h.number in compiled and h.number not in seen:
            targets.append(compiled[h.number])
            seen.add(h.number)
    targets = targets[:max_screens]

    results: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        for opp in targets:
            criteria = _load_criteria(cur, opp["id"])
            if not criteria:
                continue
            result = analyze(profile, criteria)
            store_analysis(cur, profile_id, opp["id"], criteria, result)
            counts = {"met": 0, "not_met": 0, "needs_info": 0}
            questions: list[str] = []
            for v in result["verdicts"]:
                counts[v.verdict] += 1
                if v.follow_up_question:
                    questions.append(v.follow_up_question)
            status = ("not_eligible" if counts["not_met"]
                      else "likely" if counts["needs_info"] else "eligible")
            results.append({
                "opportunity": opp,
                "counts": counts,
                "status": status,
                "open_questions": questions,
                "verdicts": [v.model_dump() for v in result["verdicts"]],
            })

    results.sort(key=lambda r: (STATUS_ORDER[r["status"]],
                                r["counts"]["needs_info"], -r["counts"]["met"]))
    return results
