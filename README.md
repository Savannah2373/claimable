# Claimable

[![evals](https://github.com/Savannah2373/claimable/actions/workflows/ci.yml/badge.svg)](https://github.com/Savannah2373/claimable/actions/workflows/ci.yml)

**A trustworthy eligibility-screening engine.** Describe yourself in plain
English; Claimable screens you against government grants and benefit programs,
reasons through the rules **clause-by-clause with a citation to the official
text for every verdict**, asks only for the facts it's missing, and refuses to
guess. Built as a study in making an LLM system you can actually *trust* with a
high-stakes answer.

**🎬 [Watch the 3-minute demo](docs/demo.mp4)** — one text box → every relevant
program screened in parallel → cited 🟢/🟡/🔴 verdicts → answer the open
questions once → a requirement-by-requirement action plan.

US-only, **140 compiled rulebooks**: 124 live Grants.gov opportunities + 16
federal benefit programs (SNAP, Medicaid/CHIP, EITC, SSI/SSDI, TANF, LIHEAP,
Section 8, Medicare, WIC, Head Start, Child Tax Credit, ACA subsidies,
Unemployment, VA Pension, Weatherization, Lifeline).

## Why this is more than a wrapper

The whole project is organized around one hard problem: **an eligibility answer
has to be right, and has to be defensible.** Every design decision falls out of
that.

- **Compile, don't prompt.** Each rulebook is decomposed *once* into atomic,
  citation-linked criteria (`compiler.py`); screening then reasons over ~10
  criteria, not 80 pages of policy. A criterion whose `source_quote` can't be
  found verbatim in the source is flagged, not stored quietly.
- **Numbers never come from the LLM.** Criteria that reduce to a threshold
  (`monthly_gross_income_usd ≤ limit`) are checked in plain Python by a
  deterministic node (`engine.py`). The model only makes judgment calls.
- **It refuses to guess.** A fact missing from the profile becomes a
  `needs_info` verdict with a concrete follow-up question — never an
  assumption. Answer once, and every affected program re-screens at the same time.
- **An independent verifier.** A second agent re-checks the risky verdicts
  against the cited rule text and downgrades anything unsupported — so a
  "you qualify" is never shipped on the model's word alone.
- **A safety metric, gated in CI.** The eval harness tracks **MET-precision** —
  it must *never* falsely tell someone they qualify — and holds it at **1.00**
  or the build fails.
- **Drift monitoring.** Rules change under you. `check_drift.py` re-fetches
  live sources, compares hashes against the exact text each rulebook was
  compiled from, and marks stale analyses for recompile.

## How it works

```
INGESTION                          COMPILE (once per rulebook)
Grants.gov API ──┐                 ┌──────────────────────────────┐
Benefit  URLs ───┼─► Postgres ────►│ Criteria Compiler (Claude,   │
USAspending API ─┘   + pgvector    │ structured outputs):         │
                                   │ policy text → atomic criteria│
                                   │ each w/ a verbatim source    │
                                   │ quote + optional machine-    │
                                   │ checkable threshold spec     │
                                   └──────────────┬───────────────┘
QUERY TIME (LangGraph)                            ▼
profile ─► DETERMINISTIC node ─► ANALYST ─► VERIFIER ─► verdicts
           thresholds checked     judges the  re-checks the │
           in pure Python —       rest; never  "met"s;      ▼
           numbers NEVER by       guesses      unsupported PLANNER
           the LLM                             → needs_info  action plan
                │                                            (+ real award
                └── needs_info → INTAKE agent (Haiku) ◄─── your answers
                    structures free-text answers into facts, re-runs
```

## Eval results

| Metric | Result | Gate |
|---|---|---|
| Criteria-extraction F1 (golden set: 6 rulebooks incl. SNAP, 35 criteria) | **1.00** | ≥ 0.75 |
| Verdict accuracy (25 labeled profile×rulebook verdicts) | **0.92** | ≥ 0.80 |
| **MET-precision — safety metric** (a false "met" = a person misled) | **1.00** (18/18) | = 1.00 |
| Citation integrity (compiled quotes found verbatim in source) | **36/36** | — |

The handful of verdict misses are conservative disagreements (`needs_info` where
a human might say `met`) — the *safe* direction, and documented rather than
tuned away; the harness exists to surface exactly these. Gates run in CI: the
extraction suite on every PR from fixtures (no API key needed), the verdict
suite on push when a key is present.

*Field note:* the drift monitor's first real catch was our own bug — Grants.gov
returns applicant-type lists in random order between calls, which made source
hashes flap until the compiler learned to sort them.

## Cost engineering

Screening is the hot path (an analyst + verifier call per program), so it's
tiered deliberately:

- **Model tiering** — compile (one-time, quality-critical) runs on Opus;
  screen-time judgment runs on **Sonnet**; intake structuring on **Haiku**.
- **Prompt caching** on the static analyst/verifier system prompts (~10% cost
  after the first call in a run).
- **Verify only what's risky** — the verifier's second call is skipped unless a
  program produced a `met` verdict, since only a false `met` can mislead someone.
- **A free pre-LLM filter** — applicant type and jurisdiction narrow the
  candidate set before any paid call; a relevance cap bounds each run.
- Every call is traced to Postgres with tokens, latency, and cost (`costs.py`).

## Scope: built to generalize, deliberately narrowed

The engine is jurisdiction-agnostic by design, and that was *proven*, not
claimed — it ran against Australia (GrantConnect), the EU (Funding & Tenders
Portal), and Singapore (Enterprise Singapore) through the same compiler and
engine with zero core changes. Those adapters and rulebooks still live in the
repo behind `discovery.ACTIVE_REGIONS`, archived out of the live product. The
narrowing to US-only was a judgment call about focus, not a limit of the
architecture.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

colima start --cpu 2 --memory 2      # once per boot (or any Docker engine)
docker compose up -d                 # Postgres 16 + pgvector on :5433
docker compose exec -T db psql -U claimable -d claimable < db/schema.sql

# 1 · ingest US grants (no key) + the federal benefit catalog
python scripts/fetch_opportunities.py --rows 150 --synopsis
python scripts/load_opportunities.py
python scripts/ingest_benefits.py
python scripts/embed_opportunities.py

# 2 · compile rulebooks (needs ANTHROPIC_API_KEY in .env, ~10¢ each)
python scripts/batch_compile.py --grants 25
python scripts/ingest_benefits.py    # then compile the benefit numbers it prints
python scripts/seed_profiles.py

# 3 · use it
streamlit run app.py                 # the one-text-box UI
python scripts/discover.py --profile "Maria R. (synthetic persona)"  # or CLI
python scripts/run_evals.py          # the eval gate
python scripts/costs.py              # LLM spend by component
```

The dormant international adapters (`scripts/fetch_grantconnect.py`,
`fetch_eu_portal.py`, `ingest_enterprisesg.py`) still run if you flip
`ACTIVE_REGIONS`.

## Layout

```
claimable/               core package
  ingestion/             grants_gov (Search2 API) · benefits (policy pages) ·
                         grantconnect · eu_portal · html_text (shared,
                         citation-critical HTML→text)
  enrichment/            usaspending (real award history)
  compiler.py            rulebook → atomic, cited, versioned criteria
  engine.py              LangGraph: deterministic → analyst → verifier
  discovery.py           free applicability filter + parallel screening
  intake.py              free-text answers → structured facts (Haiku)
  planner.py             verdicts → action plan
  search.py / rerank.py  hybrid retrieval (RRF) + cross-encoder rerank
  llm.py                 traced LLM access (tokens, latency, cost → Postgres)
scripts/                 runnable entry points (fetch, compile, screen, evals…)
evals/                   golden criteria + golden verdicts + CI fixtures
db/schema.sql            opportunities · criteria (versioned) · profiles · …
app.py                   Streamlit UI
.github/workflows/ci.yml eval gates
```

## Honest limitations

- **Federal-level, not state-specific (yet).** Benefit rulebooks are compiled
  from federal sources. For state-administered programs (SNAP, Medicaid, TANF…)
  the real income/asset limits vary by state — a verdict here is an accurate
  *federal-baseline* screen, not the final state determination. State rulebooks
  are the natural next layer.
- **Some sources resist plain fetches.** A few programs (school-meal NSLP, Pell,
  the ended ACP) are bot-blocked or JavaScript-rendered and are documented as
  deferred, not silently skipped.
- **Synthetic personas only** — no real PII anywhere in the system.

> Claimable is a screening tool, not legal or financial advice. Eligibility is
> always determined by the issuing agency.
```