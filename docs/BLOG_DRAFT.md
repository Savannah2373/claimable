# Compile, don't prompt: what I learned building an AI that tells people whether they qualify for money

*Draft — personalize before publishing.*

Claimable screens applicants against government funding rules — federal grants
and benefit programs — and answers "do you qualify?" with a citation for every
claim. Three engineering decisions ended up mattering far more than any prompt.

## 1. Compile the rulebook once; never reason over raw pages

The naive RAG design stuffs the funding notice into context and asks the model
whether the applicant qualifies. It sort of works, and it's impossible to test.

Instead, a **Criteria Compiler** decomposes each rulebook — a Grants.gov
notice, the federal SNAP policy — into atomic criteria at ingestion:
one testable requirement each, a verbatim `source_quote`, and, where the check
is mechanical, a structured threshold spec like
`{"profile_fact": "requested_funding_usd", "operator": "lte", "value": 600000}`.

Quotes are verified to appear character-for-character in the source before
anything is stored (36/36 across six rulebooks). Compilation is measurable
(extraction F1 against a hand-labeled golden set), cacheable, and versioned —
rule changes supersede old criteria instead of overwriting them, so past
verdicts stay auditable.

## 2. My RAG system is banned from doing math

Anything with a threshold spec never reaches the model. A deterministic node
compares the profile fact against the bound in plain Python. The LLM decides
only genuinely judgment-shaped questions ("is this a Qualified Youth Corps?"),
and a second, independent verifier agent re-checks every LLM verdict against
the cited rule text — anything unsupported is downgraded to "needs more info",
never shipped.

That third verdict — `needs_info`, with a concrete follow-up question — is the
load-bearing one. The prompt's hardest rule is *never guess*: a missing fact is
a question for the user, not an assumption. An intake agent (a small, cheap
model — this step is structuring, not judgment) converts the user's free-text
answer into typed facts and the engine re-runs. In the eval set this shows up
as the metric I optimize above all others: **MET-precision = 1.00** — of every
"you meet this requirement" the system produced, zero were wrong. Accuracy can
be negotiated; telling someone they qualify when they don't cannot.

## 3. The drift monitor's first catch was… me

Government rules change under you, so every compile snapshots a hash of the
exact source text, and a scheduled job re-fetches live sources and compares.
Its very first run flagged two of six sources as changed.

Neither had changed. Grants.gov returns the eligible-applicant-types list in
**nondeterministic order** between API calls, so the assembled source text
hashed differently every time. One `sorted()` later, the monitor went quiet —
and I got a free lesson: before you can detect drift in someone else's data,
you have to eliminate the nondeterminism in your own pipeline.

## The numbers

Six rulebooks (five live federal grants + SNAP), one engine. Criteria
extraction F1 1.00 against hand-labeled goldens; verdict accuracy 0.96 on 25
labeled verdicts; the one miss is a genuinely debatable `needs_info` vs
`not_met` call that I kept in the eval output instead of tuning away. Every
LLM call is traced (component, tokens, latency, cost) — a full screening run
costs about $0.25, of which the intake step costs a tenth of a cent because it
runs on a small model.

The stack: Postgres + pgvector (hybrid search, RRF fusion, cross-encoder
rerank), LangGraph for the agent pipeline, Claude with structured outputs,
Streamlit on top. All data sources are free and public: Grants.gov,
USDA FNS, USAspending.
