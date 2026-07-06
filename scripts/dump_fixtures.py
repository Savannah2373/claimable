#!/usr/bin/env python3
"""Export the compiled opportunities + current criteria as a JSON fixture so
CI can run the criteria-extraction eval suite against a fresh database with
no API key and no live fetches.

Usage:
    python scripts/dump_fixtures.py          # writes evals/fixtures/eval_data.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.db import connect

FIXTURE = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "eval_data.json"


def main() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT o.id, o.source, o.source_id, o.number, o.title
               FROM opportunities o
               JOIN criteria c ON c.opportunity_id = o.id AND c.superseded_at IS NULL
               ORDER BY o.number"""
        )
        opportunities = [
            {"id": r[0], "source": r[1], "source_id": r[2], "number": r[3], "title": r[4]}
            for r in cur.fetchall()
        ]
        cur.execute(
            """SELECT c.opportunity_id, c.criterion_key, c.version, c.text, c.category,
                      c.check_type, c.source_quote, c.threshold
               FROM criteria c
               WHERE c.superseded_at IS NULL ORDER BY c.id"""
        )
        criteria = [
            {"opportunity_id": r[0], "criterion_key": r[1], "version": r[2], "text": r[3],
             "category": r[4], "check_type": r[5], "source_quote": r[6], "threshold": r[7]}
            for r in cur.fetchall()
        ]

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(
        {"opportunities": opportunities, "criteria": criteria}, indent=1, default=str
    ))
    print(f"Wrote {len(opportunities)} opportunities, {len(criteria)} criteria → {FIXTURE}")


if __name__ == "__main__":
    main()
