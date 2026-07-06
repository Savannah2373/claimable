#!/usr/bin/env python3
"""Cost & latency report from the llm_calls trace table.

Usage:
    python scripts/costs.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.db import connect


def main() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT component, model, count(*),
                      sum(input_tokens), sum(output_tokens),
                      round(avg(latency_ms)), round(sum(est_cost_usd), 4)
               FROM llm_calls
               GROUP BY component, model
               ORDER BY sum(est_cost_usd) DESC NULLS LAST"""
        )
        rows = cur.fetchall()
        cur.execute("SELECT count(*), round(sum(est_cost_usd), 4) FROM llm_calls")
        total_calls, total_cost = cur.fetchone()

    if not rows:
        print("No traced calls yet.")
        return
    print(f"{'COMPONENT':<12} {'MODEL':<20} {'CALLS':>6} {'TOK IN':>9} {'TOK OUT':>8} "
          f"{'AVG MS':>7} {'COST $':>8}")
    print("-" * 78)
    for comp, model, n, tin, tout, ms, cost in rows:
        print(f"{comp:<12} {model:<20} {n:>6} {tin or 0:>9} {tout or 0:>8} "
              f"{int(ms or 0):>7} {cost or 0:>8}")
    print("-" * 78)
    print(f"{'TOTAL':<41} {total_calls:>6} {'':>26} {total_cost or 0:>8}")


if __name__ == "__main__":
    main()
