"""The eligibility engine — a LangGraph pipeline.

    DETERMINISTIC — criteria compiled with a structured threshold are checked
               in plain Python: one profile fact vs. one bound. Numbers are
               never compared by the LLM. Missing fact → needs_info.
    ANALYST  — the remaining criteria go to the LLM, which issues one verdict
               per criterion: met / not_met / needs_info. It must never guess:
               a fact missing from the profile is a needs_info verdict with a
               concrete follow-up question.
    VERIFIER — independently re-checks every LLM met/not_met verdict against
               the criterion's source quote and the profile facts. Any verdict
               it cannot support is downgraded to needs_info. A verdict that
               survives is stored with verified = TRUE.

Intake, scout, and planner nodes from the design brief attach to the same
state shape later.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from claimable.llm import traced_parse

load_dotenv()

ENGINE_MODEL = os.environ.get("ENGINE_MODEL", "claude-opus-4-8")

ANALYST_SYSTEM = """\
You are the eligibility analyst for a screening system. You receive an
applicant profile (structured facts) and a list of eligibility criteria
compiled from one government funding opportunity. Issue exactly one verdict
per criterion.

Verdicts:
- "met"        — the profile facts clearly satisfy the criterion.
- "not_met"    — the profile facts clearly violate the criterion.
- "needs_info" — the profile does not contain the fact needed to decide.

Hard rules:
- NEVER GUESS. If the fact isn't in the profile, the verdict is needs_info —
  even when a guess seems safe. Absence of evidence is not evidence.
- Every needs_info verdict must include ONE concrete follow_up_question that,
  if answered, would resolve the criterion.
- A criterion that imposes no obligation on this applicant (e.g. "no cost
  sharing is required") is met.
- reasoning: one or two sentences naming the specific profile fact (or the
  missing fact) that drives the verdict.
- Cover every criterion_key you were given, each exactly once.
"""

VERIFIER_SYSTEM = """\
You are the verification agent. For each verdict below, decide whether it is
actually supported: does the cited rule text, applied to the stated profile
facts, genuinely entail the verdict? Mark supported=false when the reasoning
guesses at a fact not present in the profile, misreads the rule text, or the
verdict simply does not follow. needs_info verdicts are supported when the
fact truly is absent from the profile. Be strict: your false positives become
wrong eligibility answers shown to real people.
"""


class CriterionVerdict(BaseModel):
    criterion_key: str
    verdict: Literal["met", "not_met", "needs_info"]
    reasoning: str
    follow_up_question: str | None = Field(
        default=None, description="required when verdict is needs_info, else null"
    )


class AnalystOutput(BaseModel):
    verdicts: list[CriterionVerdict]


class VerificationCheck(BaseModel):
    criterion_key: str
    supported: bool
    note: str = Field(description="one sentence: why supported or not")


class VerifierOutput(BaseModel):
    checks: list[VerificationCheck]


class EngineState(TypedDict):
    profile: dict[str, Any]  # {"name", "kind", "attrs"}
    criteria: list[dict[str, Any]]  # rows: criterion_key, text, check_type, source_quote, threshold
    llm_criteria: list[dict[str, Any]]  # criteria without a threshold → analyst
    det_verdicts: list[CriterionVerdict]  # computed in code, not by the LLM
    verdicts: list[CriterionVerdict]  # final merged output
    checks: dict[str, VerificationCheck]  # keyed by criterion_key


_OPS = {
    "lte": lambda fact, bound: float(fact) <= bound,
    "gte": lambda fact, bound: float(fact) >= bound,
    "eq": lambda fact, bound: float(fact) == bound,
    "is_true": lambda fact, _: bool(fact) is True,
    "is_false": lambda fact, _: bool(fact) is False,
}


def deterministic_node(state: EngineState) -> dict[str, Any]:
    """Mechanical checks in code. The LLM never sees these criteria."""
    attrs = state["profile"].get("attrs", {})
    det_verdicts: list[CriterionVerdict] = []
    llm_criteria: list[dict[str, Any]] = []

    for c in state["criteria"]:
        t = c.get("threshold")
        if not t or t["operator"] not in _OPS:
            llm_criteria.append(c)
            continue
        fact = attrs.get(t["profile_fact"])
        if fact is None:
            det_verdicts.append(
                CriterionVerdict(
                    criterion_key=c["criterion_key"],
                    verdict="needs_info",
                    reasoning=(
                        f"Deterministic check: profile fact '{t['profile_fact']}' "
                        f"is not provided."
                    ),
                    follow_up_question=(
                        f"Please provide '{t['profile_fact']}' — needed to check: {c['text']}"
                    ),
                )
            )
            continue
        ok = _OPS[t["operator"]](fact, t.get("value"))
        det_verdicts.append(
            CriterionVerdict(
                criterion_key=c["criterion_key"],
                verdict="met" if ok else "not_met",
                reasoning=(
                    f"Deterministic check: {t['profile_fact']} = {fact!r} "
                    f"{t['operator']} {t.get('value')!r} → {'pass' if ok else 'fail'}."
                ),
            )
        )
    return {"det_verdicts": det_verdicts, "llm_criteria": llm_criteria}


def _payload(state: EngineState) -> str:
    return json.dumps(
        {
            "applicant_profile": state["profile"],
            "criteria": [
                {
                    "criterion_key": c["criterion_key"],
                    "requirement": c["text"],
                    "check_type": c["check_type"],
                    "rule_text": c["source_quote"],
                }
                for c in state["llm_criteria"]
            ],
        },
        indent=2,
    )


def analyst_node(state: EngineState) -> dict[str, Any]:
    if not state["llm_criteria"]:
        return {"verdicts": []}
    response = traced_parse(
        "analyst",
        model=ENGINE_MODEL,
        max_tokens=16000,
        system=ANALYST_SYSTEM,
        messages=[{"role": "user", "content": _payload(state)}],
        output_format=AnalystOutput,
    )
    return {"verdicts": response.parsed_output.verdicts}


def verifier_node(state: EngineState) -> dict[str, Any]:
    checks: dict[str, VerificationCheck] = {}
    downgraded: list[CriterionVerdict] = []

    if state["verdicts"]:  # LLM verdicts to verify
        payload = json.dumps(
            {
                "applicant_profile": state["profile"],
                "verdicts_to_verify": [
                    {
                        "criterion_key": v.criterion_key,
                        "rule_text": next(
                            (c["source_quote"] for c in state["criteria"]
                             if c["criterion_key"] == v.criterion_key),
                            "",
                        ),
                        "verdict": v.verdict,
                        "reasoning": v.reasoning,
                    }
                    for v in state["verdicts"]
                ],
            },
            indent=2,
        )
        response = traced_parse(
            "verifier",
            model=ENGINE_MODEL,
            max_tokens=16000,
            system=VERIFIER_SYSTEM,
            messages=[{"role": "user", "content": payload}],
            output_format=VerifierOutput,
        )
        checks = {c.criterion_key: c for c in response.parsed_output.checks}

        # Unsupported met/not_met verdicts are downgraded, never shipped.
        for v in state["verdicts"]:
            check = checks.get(v.criterion_key)
            if check is not None and not check.supported and v.verdict != "needs_info":
                v = v.model_copy(
                    update={
                        "verdict": "needs_info",
                        "follow_up_question": v.follow_up_question
                        or f"Please confirm: {v.reasoning}",
                        "reasoning": f"{v.reasoning} [downgraded by verifier: {check.note}]",
                    }
                )
            downgraded.append(v)

    # Deterministic verdicts are mechanical — verified by construction.
    for v in state["det_verdicts"]:
        checks[v.criterion_key] = VerificationCheck(
            criterion_key=v.criterion_key, supported=True, note="computed deterministically"
        )

    # Merge back into the opportunity's criteria order.
    by_key = {v.criterion_key: v for v in downgraded + state["det_verdicts"]}
    merged = [by_key[c["criterion_key"]] for c in state["criteria"] if c["criterion_key"] in by_key]
    return {"verdicts": merged, "checks": checks}


def build_graph():
    graph = StateGraph(EngineState)
    graph.add_node("deterministic", deterministic_node)
    graph.add_node("analyst", analyst_node)
    graph.add_node("verifier", verifier_node)
    graph.add_edge(START, "deterministic")
    graph.add_edge("deterministic", "analyst")
    graph.add_edge("analyst", "verifier")
    graph.add_edge("verifier", END)
    return graph.compile()


def analyze(profile: dict[str, Any], criteria: list[dict[str, Any]]) -> EngineState:
    return build_graph().invoke(
        {
            "profile": profile,
            "criteria": criteria,
            "llm_criteria": [],
            "det_verdicts": [],
            "verdicts": [],
            "checks": {},
        }
    )


def store_analysis(
    cur,
    profile_id: int,
    opportunity_id: int,
    criteria: list[dict[str, Any]],
    result: EngineState,
) -> int:
    cur.execute(
        """INSERT INTO analyses (profile_id, opportunity_id, status, model)
           VALUES (%s, %s, 'complete', %s) RETURNING id""",
        (profile_id, opportunity_id, ENGINE_MODEL),
    )
    analysis_id = cur.fetchone()[0]
    ids_by_key = {c["criterion_key"]: c["id"] for c in criteria}
    quotes_by_key = {c["criterion_key"]: c["source_quote"] for c in criteria}
    for v in result["verdicts"]:
        check = result["checks"].get(v.criterion_key)
        cur.execute(
            """INSERT INTO verdicts
                 (analysis_id, criterion_id, verdict, reasoning,
                  citation_quote, verified, follow_up_question)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                analysis_id,
                ids_by_key[v.criterion_key],
                v.verdict,
                v.reasoning,
                quotes_by_key.get(v.criterion_key),
                bool(check and check.supported),
                v.follow_up_question,
            ),
        )
    return analysis_id
