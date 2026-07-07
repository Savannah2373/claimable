# Claimable

[![evals](https://github.com/Savannah2373/claimable/actions/workflows/ci.yml/badge.svg)](https://github.com/Savannah2373/claimable/actions/workflows/ci.yml)

**The "money you're entitled to" engine.** Claimable matches applicants to
government money — federal grants for organizations, benefit programs for
individuals — then reasons through eligibility **clause-by-clause with
citations to the actual rules**, asks for exactly the facts it's missing, and
generates a requirement-by-requirement action plan.

**🎬 [Watch the 3-minute demo](docs/demo.mp4)** — describe yourself in plain
English, watch every applicable program get screened in parallel, answer the
open questions once, get an action plan.

One engine, two proven verticals, **35 compiled rulebooks**: 30 live
**Grants.gov** opportunities and 5 federal benefit programs (**SNAP, WIC,
EITC, LIHEAP, Lifeline**) run through the *same* compiler and the *same*
eligibility engine — 194 criteria, every one citing the official text.

**The app is one text box.** Describe yourself or your organization in plain
English; an intake agent builds your structured profile; a free applicability
filter (from official applicant-type metadata) picks every rulebook your kind
of applicant could use; the full engine screens all of them in parallel and
ranks the results 🟢 eligible / 🟡 likely / 🔴 not eligible. Then you answer
the open questions **once** — the answers apply to every program at the same
time — and the yellows re-screen.

Full design doc: `../CLAIMABLE_BRIEF.md`.

## How it works

```
INGESTION                          COMPILE (once per rulebook)
Grants.gov API ──┐                 ┌──────────────────────────────┐
SNAP/policy URLs ┼─► Postgres ────►│ Criteria Compiler (Claude,   │
USAspending API ─┘   + pgvector    │ structured outputs):         │
                                   │ 80 pages → atomic criteria,  │
                                   │ each with a verbatim source  │
                                   │ quote + optional machine-    │
                                   │ checkable threshold spec     │
                                   └──────────────┬───────────────┘
QUERY TIME (LangGraph)                            ▼
profile ─► DETERMINISTIC node ─► ANALYST ─► VERIFIER ─► verdicts
           thresholds checked     LLM judges   independent      │
           in pure Python —       the rest;    re-check; un-    ▼
           numbers NEVER by       never        supported →   PLANNER
           the LLM                guesses      needs_info    action plan
                │                                             (+ real award
                └── needs_info → INTAKE agent (Haiku) ◄─── your answers
                    structures free-text answers into facts, re-runs
```

Design decisions worth reading the code for:

- **Compile, don't prompt.** Rulebooks are decomposed once into atomic,
  citation-linked criteria (`claimable/compiler.py`); analysis then reasons
  over ~10 criteria, not 80 pages. Every criterion's `source_quote` is
  verified to appear verbatim in the source — a criterion we can't cite is a
  criterion we don't store quietly.
- **Numbers never come from the LLM.** Criteria with structured thresholds
  (`requested_funding_usd ≤ 600000`) are checked in plain Python by the
  deterministic node (`claimable/engine.py`). The LLM only handles judgment.
- **Refuses to guess.** A fact missing from the profile is a `needs_info`
  verdict with a concrete follow-up question — never an assumption. The
  intake agent turns your free-text answer into structured facts and the
  engine re-runs.
- **Independent verification.** A second agent re-checks every LLM verdict
  against the cited rule text; anything unsupported is downgraded, never shipped.
- **Versioned rules + drift monitoring.** Recompiles supersede rather than
  overwrite; `scripts/check_drift.py` re-fetches live sources, compares
  hashes, and marks analyses stale when the rules change under you.
- **Model routing + tracing.** Frontier model for judgment and planning,
  Haiku for intake structuring; every call is traced to Postgres with tokens,
  latency, and cost (`scripts/costs.py`).

## Eval results (2026-07-06)

| Metric | Result | Gate |
|---|---|---|
| Criteria extraction F1 (6 rulebooks incl. SNAP, 35 required golden criteria) | **1.00** | ≥ 0.75 |
| Verdict accuracy (4 profile×rulebook cases, 25 labeled verdicts) | **0.96** (24/25) | ≥ 0.80 |
| MET-precision — safety metric (a false "met" = a person misled) | **1.00** (18/18) | = 1.00 |
| Citation integrity (compiled quotes found verbatim in source) | **36/36** | — |

The one verdict miss is a genuinely debatable judgment call (`needs_info` vs
`not_met` where the rule text allows unspecified "other eligible entities").
Documented rather than tuned away — the harness exists to surface exactly
these disagreements. Gates run in CI (`.github/workflows/ci.yml`): the
extraction suite on every PR from fixtures (no API key needed), the verdict
suite on pushes when a key secret is present.

Field note: the drift monitor's first real catch was our own nondeterminism —
Grants.gov returns applicant-type lists in random order between calls, which
made source hashes flap until the compiler sorted them.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

colima start --cpu 2 --memory 2      # once per boot (or any Docker engine)
docker compose up -d                 # Postgres 16 + pgvector on :5433
docker compose exec -T db psql -U claimable -d claimable < db/schema.sql  # first time

# 1 · ingest live grants (no key needed) + SNAP policy
python scripts/fetch_opportunities.py --rows 150 --synopsis
python scripts/load_opportunities.py
python scripts/embed_opportunities.py
python scripts/ingest_snap.py

# 1b · optional: Australia's GrantConnect (grants.gov.au) — the international
#      proof of generality; same loader, same compiler, zero engine changes
python scripts/fetch_grantconnect.py --rows 20
python scripts/load_opportunities.py
python scripts/embed_opportunities.py

# 2 · compile rulebooks + seed synthetic personas (needs ANTHROPIC_API_KEY in .env)
python scripts/compile_criteria.py HRSA-26-050
python scripts/compile_criteria.py SNAP-FEDERAL
python scripts/batch_compile.py GO8107               # any GrantConnect GO number
python scripts/seed_profiles.py

# 3 · search / discover / screen / plan
python scripts/search.py "small nonprofit doing rural health research"
python scripts/discover.py --profile "Maria R. (synthetic persona)"   # screen everything relevant
python scripts/screen.py --profile "Appalachian Rural Health Data Collaborative" \
                         --number HRSA-26-050          # interactive follow-ups
python scripts/batch_compile.py --grants 25            # grow coverage (~10¢/rulebook)
python scripts/ingest_benefits.py                      # benefit-program catalog
python scripts/run_evals.py                            # the eval gate
python scripts/costs.py                                # LLM spend by component

# 4 · or do it all in the UI
streamlit run app.py
```

## Layout

```
claimable/               core package
  ingestion/             grants_gov (Search2 API) · benefits (policy pages) ·
                         grantconnect (AU portal, server-rendered HTML)
  enrichment/            usaspending (real award history)
  compiler.py            rulebook → atomic, cited, versioned criteria
  engine.py              LangGraph: deterministic → analyst → verifier
  intake.py              free-text answers → structured facts (Haiku)
  planner.py             verdicts → action plan
  search.py / rerank.py  hybrid retrieval (RRF) + cross-encoder rerank
  llm.py                 traced LLM access (tokens, latency, cost → Postgres)
scripts/                 runnable entry points (fetch, compile, screen, evals…)
evals/                   golden criteria + golden verdicts + CI fixtures
db/schema.sql            opportunities · criteria (versioned) · profiles ·
                         analyses · verdicts · llm_calls
app.py                   Streamlit UI
.github/workflows/ci.yml eval gates
```

## Data sources (all free)

- **Grants.gov Search2 API** — live federal opportunities, no key
- **Official policy pages** — SNAP & WIC (USDA FNS), EITC (IRS), LIHEAP (HHS
  ACF), Lifeline (FCC/USAC) — the benefits vertical
- **USAspending API** — who actually wins awards under each listing, no key
- **GrantConnect (grants.gov.au)** — Australia's central grants portal, no key;
  no public API, so GO pages are parsed into the same `policy_text` shape the
  benefits vertical uses — one engine, three sources, two countries
- Profiles are **synthetic personas** — no real PII anywhere in the system

Coverage is a compile away from bigger: SSA (SSI) and Medicaid.gov bot-block
plain fetches and would need a headless browser or their official APIs —
documented as future work, not silently skipped.

> Claimable is a screening tool, not legal or financial advice. Eligibility is
> always determined by the issuing agency.
