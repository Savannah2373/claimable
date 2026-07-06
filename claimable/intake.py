"""Intake agent — turns free-text answers to follow-up questions into
structured profile facts.

Model routing: this is a cheap structuring task, so it runs on Haiku by
default while the analyst/verifier/planner stay on the frontier model.
The extracted facts feed back into the profile and the engine re-runs —
the NEEDS INFO re-entry loop from the design brief.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, Field

from claimable.llm import traced_parse

INTAKE_MODEL = os.environ.get("INTAKE_MODEL", "claude-haiku-4-5")

INTAKE_SYSTEM = """\
You convert a user's free-text answers to eligibility follow-up questions
into structured profile facts.

Rules:
- Extract ONLY facts the user actually stated. Never infer or fill gaps.
- Keys are snake_case. When a question clearly refers to one of the known
  fact keys provided, reuse that exact key.
- Dollar amounts → value_type "number", plain number (no commas/currency).
- Yes/no facts → value_type "boolean".
- Everything else → value_type "text".
- If an answer states no usable fact ("I don't know"), extract nothing for it.
"""


class Fact(BaseModel):
    key: str = Field(description="snake_case fact key")
    value_type: Literal["number", "boolean", "text"]
    number_value: float | None = None
    boolean_value: bool | None = None
    text_value: str | None = None

    def value(self) -> Any:
        return {
            "number": self.number_value,
            "boolean": self.boolean_value,
            "text": self.text_value,
        }[self.value_type]


class IntakeOutput(BaseModel):
    facts: list[Fact]


def structure_answers(
    qa_pairs: list[dict[str, str]], known_fact_keys: list[str]
) -> dict[str, Any]:
    """qa_pairs: [{"question": ..., "answer": ...}] → {fact_key: value}."""
    if not qa_pairs:
        return {}
    payload = {
        "known_fact_keys": sorted(known_fact_keys),
        "questions_and_answers": qa_pairs,
    }
    import json

    response = traced_parse(
        "intake",
        model=INTAKE_MODEL,
        max_tokens=4096,
        system=INTAKE_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload, indent=2)}],
        output_format=IntakeOutput,
    )
    return {f.key: f.value() for f in response.parsed_output.facts if f.value() is not None}
