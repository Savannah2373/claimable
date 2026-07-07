"""Claimable — describe yourself, see everything you may qualify for.

Run:  streamlit run app.py

Flow: one text box → intake agent builds your profile → every applicable
compiled rulebook is screened in parallel → ranked results → answer the open
questions once → the yellows re-screen → action plan for any program.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from claimable.db import connect

st.set_page_config(page_title="Claimable", page_icon="🧾", layout="centered")

ICONS = {"met": "✅", "not_met": "❌", "needs_info": "❓"}
BADGES = {
    "eligible": ("🟢", "Appears eligible — every published requirement met"),
    "likely": ("🟡", "Likely — nothing disqualifies you, open questions remain"),
    "not_eligible": ("🔴", "Not eligible as things stand"),
}

EXAMPLES = {
    "A small nonprofit": (
        "We're a 501(c)(3) nonprofit in Ohio with 12 staff and a $1.4M annual "
        "budget. We do data analysis and research on rural health access "
        "across Appalachian Ohio. We could put up matching funds if required."
    ),
    "A person / household": (
        "I'm a single mom in Ohio with two kids, 8 and 11. I work part-time "
        "retail about 28 hours a week and bring home about $2,400 a month "
        "before taxes, $1,850 after. We have about $1,200 in savings. "
        "I'm a US citizen and I'm not in school."
    ),
    "A state agency": (
        "We are the Ohio Department of Health's State Office of Rural Health, "
        "a state government agency designated by the governor. We can provide "
        "cost sharing and would request about $220,000."
    ),
}


def _reset_run_state() -> None:
    for k in ("profile", "profile_id", "results"):
        st.session_state.pop(k, None)


# ── sidebar: what this is ─────────────────────────────────────────────────────
with st.sidebar:
    st.title("🧾 Claimable")
    st.write(
        "Describe yourself or your organization. Claimable screens you "
        "against every government program it has compiled — federal grants "
        "and benefit programs — and shows exactly which requirements you "
        "meet, with a citation from the official rules for every answer."
    )
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT count(DISTINCT opportunity_id),
                              count(*) FILTER (WHERE superseded_at IS NULL)
                       FROM criteria""")
        n_programs, n_criteria = cur.fetchone()
    st.metric("Programs it can screen today", n_programs)
    st.caption(f"{n_criteria} compiled requirements, every one citing official text. "
               "Coverage grows one `batch_compile` at a time.")
    st.divider()
    st.caption("Screening only — eligibility is always determined by the "
               "issuing agency. Don't enter real personal details you "
               "wouldn't put in a form.")

# ── step 1: describe ──────────────────────────────────────────────────────────
st.header("Who are you?")
cols = st.columns(len(EXAMPLES))
for col, (label, text) in zip(cols, EXAMPLES.items()):
    if col.button(f"Example: {label}"):
        st.session_state["desc"] = text
        _reset_run_state()

desc = st.text_area(
    "Describe yourself or your organization — plain English is fine",
    key="desc",
    height=140,
    placeholder="e.g. I'm a part-time worker in Texas with two kids and about "
                "$2,000/month income…  or  We're a small nonprofit in Oregon that…",
)

if st.button("Find everything I might qualify for", type="primary", disabled=not desc.strip()):
    _reset_run_state()
    with st.spinner("Reading your description…"):
        from claimable.intake import profile_from_description

        profile = profile_from_description(desc)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO profiles (kind, name, attrs) VALUES (%s, %s, %s) RETURNING id",
            (profile["kind"], profile["name"], json.dumps(profile["attrs"])),
        )
        profile_id = cur.fetchone()[0]
    st.session_state["profile"] = profile
    st.session_state["profile_id"] = profile_id

    from claimable.discovery import applicable_targets, discover

    with connect() as conn:
        n_targets = len(applicable_targets(conn, profile))
    progress = st.progress(0.0, text=f"Screening {n_targets} programs (runs in parallel)…")

    def on_progress(done: int, total: int) -> None:
        progress.progress(done / total, text=f"Screened {done}/{total} programs…")

    errors: list = []
    with connect() as conn:
        st.session_state["results"] = discover(
            conn, profile_id, profile, on_progress=on_progress, errors_out=errors
        )
    st.session_state["errors"] = errors
    progress.empty()

# ── step 2: results ───────────────────────────────────────────────────────────
if "results" in st.session_state:
    profile = st.session_state["profile"]
    results = st.session_state["results"]

    st.header("What you may qualify for")
    st.caption(f"Screened as: **{profile['name']}** ({profile['kind']})")

    errors = st.session_state.get("errors") or []
    if errors:
        msg = (str(errors[0]["error"]) if errors else "").lower()
        hint = (" — the Anthropic API credit balance ran out; top up and re-run"
                if "credit balance" in msg else " — usually a temporary API error; try again")
        st.warning(f"⚠️ {len(errors)} program(s) couldn't be screened{hint}. "
                   "The results below are complete for everything that did screen.")
    with st.expander("Facts I understood from your description"):
        st.json({k: v for k, v in profile["attrs"].items() if k != "self_description"})

    n_green = sum(r["status"] == "eligible" for r in results)
    n_yellow = sum(r["status"] == "likely" for r in results)
    c1, c2, c3 = st.columns(3)
    c1.metric("🟢 Eligible", n_green)
    c2.metric("🟡 Likely", n_yellow)
    c3.metric("🔴 Not eligible", len(results) - n_green - n_yellow)

    for r in results:
        icon, label = BADGES[r["status"]]
        o, c = r["opportunity"], r["counts"]
        with st.expander(f"{icon} **{o['title'][:80]}**  ·  "
                         f"{c['met']}✅ {c['not_met']}❌ {c['needs_info']}❓"):
            st.caption(f"{o['number']} · {label}"
                       + (f" · closes {o['close_date']}" if o["close_date"] else ""))
            for v in r["verdicts"]:
                st.markdown(f"{ICONS[v['verdict']]} {v['reasoning']}")

    # ── step 3: answer once, everything re-screens ────────────────────────────
    from claimable.discovery import collect_open_questions

    questions = collect_open_questions(results)
    if questions:
        st.header("A few questions to firm things up")
        st.caption("Your answers apply to every 🟡 program at once.")
        with st.form("answers"):
            answers: dict[str, str] = {}
            for i, q in enumerate(questions):
                answers[q] = st.text_input(q, key=f"q_{i}")
            submitted = st.form_submit_button("Answer & re-check the yellows")
        if submitted:
            qa = [{"question": q, "answer": a} for q, a in answers.items() if a.strip()]
            if qa:
                with st.spinner("Structuring your answers…"):
                    from claimable.intake import structure_answers

                    new_facts = structure_answers(qa, known_fact_keys=list(profile["attrs"]))
                if new_facts:
                    st.info("Learned: " + ", ".join(f"{k} = {v!r}" for k, v in new_facts.items()))
                    profile["attrs"].update(new_facts)
                    with connect() as conn, conn.cursor() as cur:
                        cur.execute("UPDATE profiles SET attrs = %s WHERE id = %s",
                                    (json.dumps(profile["attrs"]), st.session_state["profile_id"]))

                    from claimable.discovery import STATUS_ORDER, rescreen

                    yellows = [r["opportunity"]["number"] for r in results
                               if r["status"] == "likely"]
                    with st.spinner(f"Re-screening {len(yellows)} programs with your answers…"):
                        with connect() as conn:
                            fresh = rescreen(conn, st.session_state["profile_id"],
                                             profile, yellows)
                    merged = {r["opportunity"]["number"]: r for r in results}
                    merged.update({r["opportunity"]["number"]: r for r in fresh})
                    results = sorted(
                        merged.values(),
                        key=lambda r: (STATUS_ORDER[r["status"]],
                                       r["counts"]["needs_info"], -r["counts"]["met"]),
                    )
                    st.session_state["results"] = results
                    st.rerun()
                else:
                    st.warning("I couldn't extract usable facts from those answers.")

    # ── step 4: action plan ───────────────────────────────────────────────────
    st.header("Get an action plan")
    plannable = [r for r in results if r["status"] != "not_eligible"]
    if plannable:
        pick = st.selectbox(
            "Which program?", plannable,
            format_func=lambda r: f"{BADGES[r['status']][0]} {r['opportunity']['title'][:70]}",
        )
        if st.button("Draft my action plan"):
            with st.spinner("Planner agent (+ real award history where available)…"):
                from claimable.enrichment.usaspending import alns_for_opportunity, award_stats
                from claimable.planner import build_plan, render_markdown

                o = pick["opportunity"]
                with connect() as conn, conn.cursor() as cur:
                    cur.execute("SELECT raw FROM opportunities WHERE id = %s", (o["id"],))
                    raw = cur.fetchone()[0]
                alns = alns_for_opportunity(raw or {})
                stats = award_stats(alns[0]) if alns else None
                plan = build_plan(profile, o, pick["verdicts"], stats)
                st.markdown(render_markdown(plan, profile, o, pick["verdicts"]))
    else:
        st.caption("Nothing eligible or likely yet — adjust your description and re-run.")
