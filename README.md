# Claimable

**The "money you're entitled to" engine.** Matches individuals, nonprofits, and small
businesses to government money (grants, benefits, credits), reasons through eligibility
clause-by-clause with citations to the actual rules, and generates a
requirement-by-requirement application plan.

Full design: see `../CLAIMABLE_BRIEF.md`.

## Status

Week 1 walking skeleton:

- [x] Project scaffold
- [x] Grants.gov Search2 ingestion (live API, no key needed) → JSONL
- [x] Postgres + pgvector schema (`db/schema.sql`)
- [x] Load opportunities into Postgres (idempotent upsert)
- [x] Embeddings (bge-small-en-v1.5, local) + hybrid search (dense + full-text, RRF fusion)
- [x] Criteria Compiler (Claude + structured outputs → atomic, citation-linked,
      versioned criteria; needs `ANTHROPIC_API_KEY` in `.env`)
- [x] Eligibility engine (LangGraph: deterministic → analyst → verifier;
      MET / NOT MET / NEEDS INFO verdicts with follow-up questions, verifier
      downgrades unsupported verdicts)
- [x] Deterministic check node — thresholds compiled to structured specs and
      compared in code; **numbers are never compared by the LLM**
- [x] Cross-encoder reranker on hybrid search
- [x] Eval harness: golden criteria set + golden verdict set, CI-gateable
      (`scripts/run_evals.py`, non-zero exit on regression)
- [ ] Intake agent + NEEDS INFO re-entry loop
- [ ] Planner agent (eligibility matrix → action plan)
- [ ] Benefits vertical (same engine, SNAP/EITC rules) — the generalization proof
- [ ] Langfuse tracing + cost dashboards

## Eval results (baseline, 2026-07-06)

| Metric | Result | Gate |
|---|---|---|
| Criteria extraction F1 (5 grants, 25 required golden criteria) | **1.00** | ≥ 0.75 |
| Verdict accuracy (3 profile×grant cases, 15 labeled verdicts) | **0.93** (14/15) | ≥ 0.80 |
| MET-precision — the safety metric (false "met" = someone misled) | **1.00** (8/8) | = 1.00 |
| Citation integrity (compiled quotes found verbatim in source) | **26/26** | — |

The one verdict miss is a genuinely debatable call: the engine said `needs_info`
where the golden label says `not_met`, because the rule text allows
"other eligible entities" beyond state governments. Documented rather than
hidden — the harness exists to surface exactly these disagreements.

Run it: `python scripts/run_evals.py` (add `--skip-verdicts` for the free,
LLM-less extraction suite only).

## Quickstart

```bash
cd claimable
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Pull live grant opportunities from Grants.gov (no API key required)
python scripts/fetch_opportunities.py --keyword "youth education" --rows 25 --synopsis

# Results land in data/raw/ as JSONL, one opportunity per line.
# Load them into Postgres, embed, and search:
python scripts/load_opportunities.py
python scripts/embed_opportunities.py
python scripts/search.py "small nonprofit running after-school programs for teenagers"

# Compile a grant's rules into criteria, then screen a profile against them
# (needs ANTHROPIC_API_KEY in .env)
python scripts/compile_criteria.py HRSA-26-050
python scripts/seed_profiles.py
python scripts/analyze.py --profile "Appalachian Rural Health Data Collaborative" \
                          --number HRSA-26-050
```

## Database

Docker runs via [colima](https://github.com/abiosoft/colima) (lightweight CLI Docker,
installed via `brew install colima docker docker-compose`) — no Docker Desktop needed.

```bash
colima start --cpu 2 --memory 2   # once per boot; starts the Docker VM
docker compose up -d              # Postgres 16 + pgvector on localhost:5433
docker compose exec -T db psql -U claimable -d claimable < db/schema.sql   # first time only
```

## Layout

```
claimable/            Python package
  ingestion/          source-specific API clients (grants_gov, sam_gov, ...)
  config.py           env-driven settings
scripts/              runnable entry points
db/schema.sql         full schema: opportunities, documents, chunks, criteria,
                      profiles, analyses, verdicts (see brief §3)
data/raw/             fetched JSONL (gitignored)
tests/                pytest
```

## Data sources

- [Grants.gov Search2 API](https://api.grants.gov/v1/api/search2) — live federal
  grant opportunities, no key required
- SAM.gov, USAspending, eCFR — planned (see brief §4)

> Claimable is a screening tool, not legal or financial advice. Eligibility is
> always determined by the issuing agency.
