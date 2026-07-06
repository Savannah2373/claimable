"""The Criteria Compiler — the heart of Claimable.

Takes one opportunity's official text (synopsis + eligibility description +
structured metadata from Grants.gov) and compiles it into atomic, testable,
citation-linked eligibility criteria. Compiled once at ingestion; the
eligibility engine then reasons over criteria, never over the raw 80 pages.

Every criterion must carry a source_quote — an exact sentence from the input.
After the LLM call we verify each quote actually appears in the source text;
unverifiable quotes are flagged, because a criterion we can't cite is a
criterion we can't trust.
"""

from __future__ import annotations

import html
import os
import re
from typing import Any, Literal

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

COMPILER_MODEL = os.environ.get("COMPILER_MODEL", "claude-opus-4-8")

SYSTEM_PROMPT = """\
You are the Criteria Compiler for an eligibility-screening system. Your input
is the official text of one government funding opportunity. Your output is the
complete list of atomic eligibility criteria an applicant must satisfy.

Rules:
- ATOMIC: one testable requirement per criterion. "Must be a nonprofit located
  in Ohio" is two criteria, not one.
- ELIGIBILITY ONLY: extract who may apply and threshold conditions
  (entity type, geography, financial limits, cost sharing, one-per-state
  rules, required registrations). Do NOT extract application logistics
  (deadlines, page limits, submission format) or evaluation/scoring criteria.
- GROUNDED: source_quote must be an EXACT contiguous substring of the input
  text — copy it verbatim, no paraphrasing, no fixing typos. Keep quotes
  under 300 characters; quote the sentence that states the requirement.
- check_type: "deterministic" when the criterion can be checked mechanically
  against a structured fact (a number vs. a threshold, membership in a listed
  entity type); "judgment" when deciding requires interpretation (e.g.
  "primarily serves rural youth").
- criterion_key: short stable snake_case identifier, e.g. "applicant_type",
  "geographic_scope", "cost_sharing".
- threshold: attach ONLY when the criterion reduces to comparing a single
  profile fact from this exact vocabulary against a bound or boolean:
    requested_funding_usd, annual_budget_usd, staff_count,
    is_state_government, is_nonprofit, designated_by_governor,
    can_provide_cost_sharing, operates_state_office_of_rural_health
  Examples: an award ceiling of $600,000 → {profile_fact:
  "requested_funding_usd", operator: "lte", value: 600000}; a cost-sharing
  requirement → {profile_fact: "can_provide_cost_sharing", operator:
  "is_true", value: null}. If the fact is not in the vocabulary, or the check
  needs interpretation, leave threshold null.
- If the text contains no eligibility information at all, return an empty list.
"""


class ThresholdSpec(BaseModel):
    """A mechanical check: one profile fact vs. one bound. Anything with a
    threshold is checked in code at analysis time — never by the LLM."""

    profile_fact: str = Field(description="fact key from the allowed vocabulary")
    operator: Literal["lte", "gte", "eq", "is_true", "is_false"]
    value: float | None = Field(
        default=None, description="numeric bound; null for is_true/is_false"
    )


class CompiledCriterion(BaseModel):
    criterion_key: str = Field(description="short stable snake_case id")
    text: str = Field(description="plain-English atomic requirement")
    category: Literal[
        "organizational", "geographic", "financial", "documentation", "other"
    ]
    check_type: Literal["deterministic", "judgment"]
    source_quote: str = Field(description="exact substring of the source text")
    threshold: ThresholdSpec | None = Field(
        default=None, description="set only for mechanical single-fact checks"
    )


class CompiledCriteria(BaseModel):
    criteria: list[CompiledCriterion]


def _strip_html(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t​﻿]+", " ", text).strip()


def build_source_text(opp_row: dict[str, Any]) -> str:
    """Assemble the labeled source document from a stored opportunity's raw
    payload (shape: {"search_hit": ..., "detail": ...} after --synopsis fetch)."""
    detail = (opp_row.get("raw") or {}).get("detail", {})
    syn = detail.get("synopsis") or {}

    sections: list[tuple[str, str]] = []
    if syn.get("synopsisDesc"):
        sections.append(("PROGRAM DESCRIPTION", _strip_html(syn["synopsisDesc"])))
    if syn.get("applicantEligibilityDesc"):
        sections.append(
            ("ADDITIONAL ELIGIBILITY INFORMATION", _strip_html(syn["applicantEligibilityDesc"]))
        )
    types = [t.get("description", "") for t in syn.get("applicantTypes", [])]
    if types:
        sections.append(("ELIGIBLE APPLICANT TYPES", "; ".join(t for t in types if t)))
    if syn.get("costSharing") is not None:
        sections.append(
            ("COST SHARING REQUIRED", "Yes" if syn["costSharing"] in (True, "Yes", "Y") else "No")
        )
    facts = []
    for label, key in [
        ("Award ceiling", "awardCeilingFormatted"),
        ("Award floor", "awardFloorFormatted"),
        ("Estimated total funding", "estimatedFundingFormatted"),
        ("Expected number of awards", "numberOfAwards"),
    ]:
        if syn.get(key):
            facts.append(f"{label}: {syn[key]}")
    if facts:
        sections.append(("AWARD INFORMATION", "\n".join(facts)))

    header = f"OPPORTUNITY: {opp_row.get('title', '')} ({opp_row.get('number', '')})"
    body = "\n\n".join(f"## {name}\n{text}" for name, text in sections)
    return f"{header}\n\n{body}"


def compile_criteria(source_text: str) -> CompiledCriteria:
    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=COMPILER_MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": source_text}],
        output_format=CompiledCriteria,
    )
    return response.parsed_output


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def verify_quotes(
    compiled: CompiledCriteria, source_text: str
) -> list[tuple[CompiledCriterion, bool]]:
    """Check each criterion's source_quote appears verbatim (whitespace-insensitive)
    in the source text. This is the compile-time citation-integrity gate."""
    haystack = _normalize(source_text)
    return [(c, _normalize(c.source_quote) in haystack) for c in compiled.criteria]


def store_criteria(cur, opportunity_id: int, compiled: CompiledCriteria) -> int:
    """Version-aware insert: supersede any current criteria for the opportunity,
    then write the new set as version N+1. Old versions stay for auditability."""
    cur.execute(
        """SELECT coalesce(max(version), 0) FROM criteria WHERE opportunity_id = %s""",
        (opportunity_id,),
    )
    new_version = cur.fetchone()[0] + 1
    cur.execute(
        """UPDATE criteria SET superseded_at = now()
           WHERE opportunity_id = %s AND superseded_at IS NULL""",
        (opportunity_id,),
    )
    for c in compiled.criteria:
        cur.execute(
            """INSERT INTO criteria
                 (opportunity_id, criterion_key, version, text, category,
                  check_type, source_quote, threshold)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                opportunity_id,
                c.criterion_key,
                new_version,
                c.text,
                c.category,
                c.check_type,
                c.source_quote,
                c.threshold.model_dump_json() if c.threshold else None,
            ),
        )
    return new_version
