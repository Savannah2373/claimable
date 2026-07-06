"""Claimable — Streamlit frontend.

Run:  streamlit run app.py
Flow: pick a profile → search opportunities (hybrid + rerank) → view compiled
criteria with citations → run the eligibility engine → answer follow-ups
(intake agent) → re-run → action plan.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from claimable.db import connect

st.set_page_config(page_title="Claimable", page_icon="🧾", layout="wide")

ICONS = {"met": "✅", "not_met": "❌", "needs_info": "❓"}


@st.cache_data(ttl=60)
def load_profiles() -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, kind, name, attrs FROM profiles ORDER BY name")
        return [{"id": r[0], "kind": r[1], "name": r[2], "attrs": r[3]} for r in cur.fetchall()]


@st.cache_data(ttl=60)
def load_compiled_opportunities() -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT o.id, o.number, o.title, o.agency_name, o.close_date::text, o.source
               FROM opportunities o
               JOIN criteria c ON c.opportunity_id = o.id AND c.superseded_at IS NULL
               ORDER BY o.number"""
        )
        return [{"id": r[0], "number": r[1], "title": r[2], "agency": r[3],
                 "close_date": r[4], "source": r[5]} for r in cur.fetchall()]


def load_criteria(opp_id: int) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, criterion_key, text, check_type, source_quote, threshold, version
               FROM criteria WHERE opportunity_id = %s AND superseded_at IS NULL ORDER BY id""",
            (opp_id,),
        )
        return [{"id": r[0], "criterion_key": r[1], "text": r[2], "check_type": r[3],
                 "source_quote": r[4], "threshold": r[5], "version": r[6]} for r in cur.fetchall()]


# ── sidebar: applicant ────────────────────────────────────────────────────────
profiles = load_profiles()
st.sidebar.title("🧾 Claimable")
st.sidebar.caption("The money-you're-entitled-to engine. Screening only — "
                   "eligibility is always determined by the issuing agency.")
profile_row = st.sidebar.selectbox(
    "Applicant profile", profiles, format_func=lambda p: f"{p['name']} ({p['kind']})"
)

attrs_key = f"attrs_{profile_row['id']}"
if attrs_key not in st.session_state:
    st.session_state[attrs_key] = dict(profile_row["attrs"])
attrs = st.session_state[attrs_key]

with st.sidebar.expander("Profile facts", expanded=False):
    st.json(attrs)

# ── main: find an opportunity ────────────────────────────────────────────────
st.header("1 · Find money")
query = st.text_input(
    "Describe the applicant or what you're looking for",
    placeholder="small nonprofit doing rural health research in Ohio",
)
if query:
    with st.spinner("Hybrid search + rerank…"):
        from claimable.search import hybrid_search

        with connect() as conn:
            hits = hybrid_search(conn, query, k=8)
    compiled_numbers = {o["number"] for o in load_compiled_opportunities()}
    st.dataframe(
        [{"number": h.number, "title": h.title, "closes": h.close_date,
          "rerank score": round(h.rerank_score or 0, 2),
          "criteria compiled": "✓" if h.number in compiled_numbers else "—"}
         for h in hits],
        use_container_width=True, hide_index=True,
    )

st.subheader("…or screen everything relevant at once")
col_a, col_b = st.columns([1, 3])
with col_a:
    max_screens = st.number_input("Programs to screen", 2, 8, 4,
                                  help="Each program screened costs LLM calls (~10–20¢).")
with col_b:
    st.caption("Discovery builds a search query from the profile itself, finds the most "
               "relevant compiled rulebooks, and runs the full engine on each. For "
               "individuals, all benefit programs are always included.")
if st.button("🔎 Discover & screen", type="primary"):
    with st.spinner(f"Screening up to {max_screens} programs — this takes a couple of minutes…"):
        from claimable.discovery import discover

        profile = {"name": profile_row["name"], "kind": profile_row["kind"], "attrs": attrs}
        with connect() as conn:
            disc = discover(conn, profile_row["id"], profile, max_screens=int(max_screens))
    st.session_state["discovery"] = disc

if "discovery" in st.session_state:
    badges = {"eligible": ("🟢", "Appears eligible"),
              "likely": ("🟡", "Likely eligible — open questions"),
              "not_eligible": ("🔴", "Not eligible as things stand")}
    for r in st.session_state["discovery"]:
        icon, label = badges[r["status"]]
        o, c = r["opportunity"], r["counts"]
        with st.expander(f"{icon} {o['number']} — {o['title'][:70]}  "
                         f"({c['met']}✅ {c['not_met']}❌ {c['needs_info']}❓)"):
            st.caption(label + (f" · closes {o['close_date']}" if o["close_date"] else ""))
            for v in r["verdicts"]:
                st.markdown(f"{ICONS[v['verdict']]} **{v['criterion_key']}** — {v['reasoning']}")
            if r["open_questions"]:
                st.markdown("**To firm this up:** select this program below and answer "
                            "the follow-ups.")

st.header("2 · Screen eligibility")
opps = load_compiled_opportunities()
opp = st.selectbox(
    "Opportunity (criteria compiled)",
    opps,
    format_func=lambda o: f"{o['number']} — {o['title'][:70]}",
)
criteria = load_criteria(opp["id"])

with st.expander(f"Compiled criteria (v{criteria[0]['version'] if criteria else '?'}, "
                 f"{len(criteria)} rules — every one cites the official text)"):
    for c in criteria:
        det = " · deterministic" if c["threshold"] else ""
        st.markdown(f"**{c['criterion_key']}**{det} — {c['text']}")
        st.caption(f"“{c['source_quote']}”")

run = st.button("Run eligibility analysis", type="primary")
result_key = f"result_{profile_row['id']}_{opp['id']}"

if run:
    with st.spinner("deterministic checks → analyst → verifier…"):
        from claimable.engine import analyze, store_analysis

        profile = {"name": profile_row["name"], "kind": profile_row["kind"], "attrs": attrs}
        result = analyze(profile, criteria)
        with connect() as conn, conn.cursor() as cur:
            store_analysis(cur, profile_row["id"], opp["id"], criteria, result)
        st.session_state[result_key] = {
            "verdicts": [v.model_dump() for v in result["verdicts"]],
            "checks": {k: c.model_dump() for k, c in result["checks"].items()},
        }

if result_key in st.session_state:
    res = st.session_state[result_key]
    counts = {"met": 0, "not_met": 0, "needs_info": 0}
    quotes = {c["criterion_key"]: c["source_quote"] for c in criteria}

    for v in res["verdicts"]:
        counts[v["verdict"]] += 1
        check = res["checks"].get(v["criterion_key"], {})
        verified = "verified" if check.get("supported") else "unverified"
        with st.container(border=True):
            st.markdown(f"{ICONS[v['verdict']]} **{v['criterion_key']}** · "
                        f"`{v['verdict']}` · _{verified}_")
            st.write(v["reasoning"])
            with st.expander("Rule text this rests on"):
                st.caption(f"“{quotes.get(v['criterion_key'], '')}”")

    if counts["not_met"]:
        st.error(f"Not eligible as things stand — {counts['met']} met · "
                 f"{counts['not_met']} not met · {counts['needs_info']} needs info")
    elif counts["needs_info"]:
        st.warning(f"Likely eligible, pending answers — {counts['met']} met · "
                   f"{counts['needs_info']} needs info")
    else:
        st.success(f"Appears eligible on all {counts['met']} published criteria")

    # ── needs-info re-entry loop ─────────────────────────────────────────────
    needs = [v for v in res["verdicts"] if v["verdict"] == "needs_info"]
    if needs:
        st.header("3 · Answer the open questions")
        with st.form("answers"):
            answers = {}
            for v in needs:
                q = v["follow_up_question"] or f"Clarify: {v['reasoning']}"
                answers[v["criterion_key"]] = st.text_input(q, key=f"ans_{v['criterion_key']}")
            submitted = st.form_submit_button("Submit answers & re-run")
        if submitted:
            qa = [{"question": (v["follow_up_question"] or v["reasoning"]),
                   "answer": answers[v["criterion_key"]]}
                  for v in needs if answers[v["criterion_key"]].strip()]
            if qa:
                with st.spinner("intake agent structuring answers…"):
                    from claimable.intake import structure_answers

                    new_facts = structure_answers(qa, known_fact_keys=list(attrs))
                if new_facts:
                    st.info("Learned: " + ", ".join(f"{k} = {v!r}" for k, v in new_facts.items()))
                    attrs.update(new_facts)
                    st.session_state.pop(result_key, None)
                    st.rerun()
                else:
                    st.warning("No usable facts extracted from those answers.")

    # ── action plan ──────────────────────────────────────────────────────────
    st.header("4 · Action plan")
    if st.button("Draft action plan"):
        with st.spinner("planner agent (+ USAspending award history)…"):
            from claimable.enrichment.usaspending import alns_for_opportunity, award_stats
            from claimable.planner import build_plan, render_markdown

            with connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT raw FROM opportunities WHERE id = %s", (opp["id"],))
                raw = cur.fetchone()[0]
            alns = alns_for_opportunity(raw)
            stats = award_stats(alns[0]) if alns else None
            profile = {"name": profile_row["name"], "kind": profile_row["kind"], "attrs": attrs}
            plan = build_plan(profile, opp, res["verdicts"], stats)
            st.markdown(render_markdown(plan, profile, opp, res["verdicts"]))
