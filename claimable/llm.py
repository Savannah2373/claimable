"""Traced LLM access. Every model call in the system goes through
traced_parse(), which records component, model, token usage, latency, and
estimated cost to the llm_calls table. `scripts/costs.py` reports on it.

This is the lightweight local observability layer; in deployment the same
choke point is where Langfuse instrumentation attaches.
"""

from __future__ import annotations

import os
import time

import anthropic
from dotenv import load_dotenv

load_dotenv()

# (input $/MTok, output $/MTok) — used for cost estimates in traces
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    inp, out = PRICES.get(model, (0.0, 0.0))
    return (input_tokens * inp + output_tokens * out) / 1_000_000


def traced_parse(component: str, **kwargs):
    """client.messages.parse() with tracing. Tracing failures never break the
    actual call — observability is best-effort by design."""
    start = time.monotonic()
    response = get_client().messages.parse(**kwargs)
    latency_ms = int((time.monotonic() - start) * 1000)

    try:
        from claimable.db import connect

        usage = response.usage
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO llm_calls
                     (component, model, input_tokens, output_tokens, latency_ms, est_cost_usd)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    component,
                    kwargs.get("model", "?"),
                    usage.input_tokens,
                    usage.output_tokens,
                    latency_ms,
                    _estimate_cost(
                        kwargs.get("model", ""), usage.input_tokens, usage.output_tokens
                    ),
                ),
            )
    except Exception as exc:  # noqa: BLE001 — tracing must never break the call
        if os.environ.get("CLAIMABLE_DEBUG"):
            print(f"[trace] failed to record llm call: {exc}")
    return response
