"""Planner agent — turns a finished eligibility analysis into an action plan:
what to do next, what documents to gather, what remains open, and how this
program's real award history (USAspending) frames expectations.

The planner NARRATES; it never re-decides eligibility. Verdicts arrive as
settled facts and the prompt forbids revisiting them.
"""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel

from claimable.llm import traced_parse

PLANNER_MODEL = os.environ.get("PLANNER_MODEL", "claude-opus-4-8")

PLANNER_SYSTEM = """\
You are the planning agent for an eligibility-screening system. You receive a
finished eligibility analysis (verdicts are settled — do NOT re-judge them),
the opportunity's metadata, and optionally real historical award statistics.

Produce a practical action plan for this specific applicant:
- headline: one plain-English sentence on where they stand.
- next_steps: concrete, ordered actions. If deadlines exist, reference them.
- documents_needed: evidence/registrations the application will require,
  grounded in the criteria (e.g. cost-sharing commitment letter when cost
  sharing is required).
- open_questions: restate every unresolved needs_info item as a question.
- award_context: 1-3 sentences interpreting the historical award stats for
  this applicant, if stats were provided; otherwise an empty list. Never
  invent numbers not present in the input.
Write for a smart non-expert. No hedging boilerplate.
"""


class Plan(BaseModel):
    headline: str
    next_steps: list[str]
    documents_needed: list[str]
    open_questions: list[str]
    award_context: list[str]


def build_plan(
    profile: dict[str, Any],
    opportunity: dict[str, Any],
    verdicts: list[dict[str, Any]],
    award_stats: dict[str, Any] | None,
) -> Plan:
    payload = {
        "applicant": {"name": profile["name"], "kind": profile["kind"]},
        "opportunity": opportunity,
        "verdicts": verdicts,
        "historical_award_stats": award_stats,
    }
    response = traced_parse(
        "planner",
        model=PLANNER_MODEL,
        max_tokens=16000,
        system=PLANNER_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload, indent=2, default=str)}],
        output_format=Plan,
    )
    return response.parsed_output


def render_markdown(
    plan: Plan,
    profile: dict[str, Any],
    opportunity: dict[str, Any],
    verdicts: list[dict[str, Any]],
) -> str:
    icons = {"met": "✅", "not_met": "❌", "needs_info": "❓"}
    lines = [
        f"# Eligibility screening: {opportunity.get('title')}",
        "",
        f"**Applicant:** {profile['name']}  ",
        f"**Opportunity:** {opportunity.get('number')} · closes {opportunity.get('close_date', 'TBD')}  ",
        "",
        f"## {plan.headline}",
        "",
        "## Eligibility matrix",
        "",
        "| | Criterion | Finding |",
        "|---|---|---|",
    ]
    for v in verdicts:
        lines.append(f"| {icons.get(v['verdict'], '?')} | {v['criterion_key']} | {v['reasoning']} |")
    if plan.next_steps:
        lines += ["", "## Next steps", ""] + [f"{i}. {s}" for i, s in enumerate(plan.next_steps, 1)]
    if plan.documents_needed:
        lines += ["", "## Documents to gather", ""] + [f"- {d}" for d in plan.documents_needed]
    if plan.open_questions:
        lines += ["", "## Open questions", ""] + [f"- {q}" for q in plan.open_questions]
    if plan.award_context:
        lines += ["", "## What awards under this program actually look like", ""] + list(plan.award_context)
    lines += ["", "---", "_Screening only — eligibility is always determined by the issuing agency._", ""]
    return "\n".join(lines)
