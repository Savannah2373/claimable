"""Discovery mode — "screen me against everything applicable."

Applicability is decided for free before any LLM call: individuals are
screened against benefit programs plus the grants whose official applicant
types include individuals; organizations against grants. Every applicable
compiled rulebook is then run through the full eligibility engine — in
parallel — and results come back ranked: eligible → likely → not eligible.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import psycopg

from claimable.db import connect
from claimable.engine import analyze, store_analysis
from claimable.search import hybrid_search

STATUS_ORDER = {"eligible": 0, "likely": 1, "not_eligible": 2}
_WORKERS = 4  # parallel screens; each worker uses its own DB connection

# Each ingestion source belongs to one funding jurisdiction. An applicant is
# only screened against programs in a jurisdiction they can actually use —
# a US benefit needs US residency, an Australian grant needs an Australian
# applicant — so this is a free, pre-LLM filter that also keeps runs cheap.
_JURISDICTION_BY_SOURCE = {
    "grants.gov": "US", "policy": "US",
    "grantconnect": "AU", "eu_portal": "EU", "enterprisesg": "SG",
}
_EU_MEMBER_STATES = {
    "austria", "belgium", "bulgaria", "croatia", "cyprus", "czechia",
    "czech republic", "denmark", "estonia", "finland", "france", "germany",
    "greece", "hungary", "ireland", "italy", "latvia", "lithuania",
    "luxembourg", "malta", "netherlands", "poland", "portugal", "romania",
    "slovakia", "slovenia", "spain", "sweden",
}


def profile_jurisdictions(profile: dict[str, Any]) -> set[str] | None:
    """Which funding jurisdictions this applicant's country can use. Returns
    None when the country can't be mapped — the caller then screens every
    jurisdiction (relying on the relevance cap for cost), never silently
    excluding someone because their country string was unfamiliar."""
    country = str((profile.get("attrs") or {}).get("country") or "").strip().lower()
    if not country:
        return None
    if country in {"united states", "united states of america", "usa", "us", "u.s.", "u.s.a."}:
        return {"US"}
    if country == "australia":
        return {"AU"}
    if country == "singapore":
        return {"SG"}
    if country in _EU_MEMBER_STATES:
        return {"EU"}
    return None  # unknown country → don't filter, let the cap bound cost


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


def applicable_targets(
    conn: psycopg.Connection, profile: dict[str, Any]
) -> list[dict[str, Any]]:
    """Every compiled rulebook this KIND of applicant could possibly use.
    Decided from official metadata — free, before any LLM call."""
    with conn.cursor() as cur:
        # two applicability signals, in trust order: official applicant-type
        # metadata (Grants.gov) wins when present; otherwise the rulebook-level
        # applicant_kinds the compiler extracted from the eligibility text
        # (sources like GrantConnect publish no structured applicant types).
        # A row with neither signal is open to both kinds — exclusion must be
        # evidenced, never assumed.
        cur.execute(
            """SELECT DISTINCT o.id, o.number, o.title, o.source, o.close_date::text,
                      CASE
                        WHEN o.raw->'detail'->'synopsis'->'applicantTypes' IS NOT NULL
                        THEN EXISTS (
                          SELECT 1 FROM jsonb_array_elements(
                            o.raw->'detail'->'synopsis'->'applicantTypes'
                          ) t WHERE t->>'description' ILIKE '%%individual%%')
                        ELSE coalesce(o.raw->'applicant_kinds' ? 'individual', true)
                      END AS allows_individuals,
                      CASE
                        WHEN o.raw->'detail'->'synopsis'->'applicantTypes' IS NOT NULL
                        THEN true  -- grants.gov listings are organization-facing
                        ELSE coalesce(o.raw->'applicant_kinds' ? 'organization', true)
                      END AS allows_organizations
               FROM opportunities o
               JOIN criteria c ON c.opportunity_id = o.id AND c.superseded_at IS NULL
               ORDER BY o.source DESC, o.close_date::text NULLS LAST"""
        )
        rows = [{"id": r[0], "number": r[1], "title": r[2], "source": r[3],
                 "close_date": r[4], "allows_individuals": r[5],
                 "allows_organizations": r[6]} for r in cur.fetchall()]

    # jurisdiction: only screen programs from a funding system this applicant's
    # country can use (skipped when the country is unknown)
    juris = profile_jurisdictions(profile)
    if juris is not None:
        rows = [o for o in rows if _JURISDICTION_BY_SOURCE.get(o["source"]) in juris]

    if profile.get("kind") == "individual":
        # benefit programs + grants whose rules accept individual applicants
        return [o for o in rows if o["source"] == "policy" or o["allows_individuals"]]
    return [o for o in rows if o["source"] != "policy" and o["allows_organizations"]]


def discover(
    conn: psycopg.Connection,
    profile_id: int,
    profile: dict[str, Any],
    max_screens: int | None = None,
    query: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    errors_out: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Screen everything applicable (default) or cap with max_screens, in
    which case search relevance decides which programs keep their slot."""
    targets = applicable_targets(conn, profile)
    if max_screens is not None and len(targets) > max_screens:
        query = query or build_profile_query(profile)
        rank = {h.number: i for i, h in enumerate(hybrid_search(conn, query, k=50))}
        targets.sort(key=lambda o: (o["source"] != "policy", rank.get(o["number"], 999)))
        targets = targets[:max_screens]
    return screen_programs(conn, profile_id, profile, targets,
                           on_progress=on_progress, errors_out=errors_out)


def _screen_one(profile_id: int, profile: dict[str, Any], opp: dict[str, Any]) -> dict[str, Any] | None:
    """Screen one program on its own DB connection (thread-worker safe).

    A per-program failure (a transient API error, a rate limit, a bad
    response) returns an {"status": "error"} marker rather than raising —
    one failed screen must never discard the whole run's successful results.
    The engine's own "never guess" contract means an errored screen is
    reported as unscreened, never as a verdict.
    """
    try:
        with connect() as conn, conn.cursor() as cur:
            criteria = _load_criteria(cur, opp["id"])
            if not criteria:
                return None
            result = analyze(profile, criteria)
            store_analysis(cur, profile_id, opp["id"], criteria, result)
    except Exception as exc:  # noqa: BLE001 — isolate one program's failure
        return {"opportunity": opp, "status": "error", "error": str(exc)}
    counts = {"met": 0, "not_met": 0, "needs_info": 0}
    questions: list[str] = []
    for v in result["verdicts"]:
        counts[v.verdict] += 1
        if v.follow_up_question:
            questions.append(v.follow_up_question)
    status = ("not_eligible" if counts["not_met"]
              else "likely" if counts["needs_info"] else "eligible")
    return {
        "opportunity": opp,
        "counts": counts,
        "status": status,
        "open_questions": questions,
        "verdicts": [v.model_dump() for v in result["verdicts"]],
    }


def screen_programs(
    conn: psycopg.Connection,  # kept for signature stability; workers connect themselves
    profile_id: int,
    profile: dict[str, Any],
    targets: list[dict[str, Any]],
    on_progress: Callable[[int, int], None] | None = None,
    errors_out: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Run the full engine on every target program in parallel; ranked summaries.

    Programs that failed to screen are kept out of the ranked results (so
    they are never miscounted as a verdict) and, when `errors_out` is given,
    appended there so the caller can report a partial run honestly.
    """
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    done = 0
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = [pool.submit(_screen_one, profile_id, profile, opp) for opp in targets]
        for f in futures:
            r = f.result()  # _screen_one never raises; errors come back as markers
            done += 1
            if on_progress:
                on_progress(done, len(targets))
            if not r:
                continue
            (errors if r["status"] == "error" else results).append(r)

    if errors_out is not None:
        errors_out.extend(errors)
    results.sort(key=lambda r: (STATUS_ORDER[r["status"]],
                                r["counts"]["needs_info"], -r["counts"]["met"]))
    return results


def rescreen(
    conn: psycopg.Connection,
    profile_id: int,
    profile: dict[str, Any],
    numbers: list[str],
) -> list[dict[str, Any]]:
    """Re-screen a specific set of programs (the answer-once loop: new facts
    in the profile apply to every program at the same time)."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT o.id, o.number, o.title, o.source, o.close_date::text
               FROM opportunities o
               JOIN criteria c ON c.opportunity_id = o.id AND c.superseded_at IS NULL
               WHERE o.number = ANY(%s)""",
            (numbers,),
        )
        by_number = {r[1]: {"id": r[0], "number": r[1], "title": r[2],
                            "source": r[3], "close_date": r[4]} for r in cur.fetchall()}
    targets = [by_number[n] for n in numbers if n in by_number]
    return screen_programs(conn, profile_id, profile, targets)


_DEDUP_SIMILARITY = 0.85  # cosine over bge embeddings; paraphrases score ~0.84+


def collect_open_questions(results: list[dict[str, Any]]) -> list[str]:
    """Deduped follow-up questions from programs that are still winnable
    (🟡 likely) — answering a 🔴 program's questions can't flip its not_met.

    Many programs independently ask for the same fact (income, household
    size, entity type), so exact-string dedup isn't enough — the applicant
    still sees near-duplicate wordings. We merge those by embedding
    similarity (free, local model — no API cost), which works across every
    region since it compares meaning, not keywords. The precise templated
    questions (engine._FACT_QUESTIONS, one per structured fact) are always
    kept and never merged into each other, so distinct facts that happen to
    read similarly — e.g. income *before* vs *after* tax — are never
    collapsed."""
    from claimable.engine import _FACT_QUESTIONS

    canonical = set(_FACT_QUESTIONS.values())
    ordered: list[str] = []
    seen_exact: set[str] = set()
    for r in results:
        if r["status"] == "likely":
            for q in r["open_questions"]:
                if q not in seen_exact:
                    seen_exact.add(q)
                    ordered.append(q)
    if len(ordered) <= 1:
        return ordered

    from claimable.embeddings import embed_documents

    vecs = dict(zip(ordered, embed_documents(ordered)))
    kept: list[str] = []
    kept_vecs: list[list[float]] = []

    def _too_similar(v: list[float]) -> bool:
        return any(sum(a * b for a, b in zip(v, kv)) >= _DEDUP_SIMILARITY
                   for kv in kept_vecs)

    # precise templated questions first: always kept, never merged away
    for q in ordered:
        if q in canonical:
            kept.append(q)
            kept_vecs.append(vecs[q])
    # then free-text questions, dropping any that duplicate a kept question
    for q in ordered:
        if q in canonical or _too_similar(vecs[q]):
            continue
        kept.append(q)
        kept_vecs.append(vecs[q])

    order = {q: i for i, q in enumerate(ordered)}
    return sorted(kept, key=order.__getitem__)
