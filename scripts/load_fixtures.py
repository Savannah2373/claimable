#!/usr/bin/env python3
"""Load evals/fixtures/eval_data.json into the database (CI setup step).

Usage:
    python scripts/load_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.db import connect

FIXTURE = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "eval_data.json"


def main() -> None:
    data = json.loads(FIXTURE.read_text())
    id_map: dict[int, int] = {}

    with connect() as conn, conn.cursor() as cur:
        for o in data["opportunities"]:
            cur.execute(
                """INSERT INTO opportunities (source, source_id, number, title, raw)
                   VALUES (%s, %s, %s, %s, '{}')
                   ON CONFLICT (source, source_id) DO UPDATE SET title = EXCLUDED.title
                   RETURNING id""",
                (o["source"], o["source_id"], o["number"], o["title"]),
            )
            id_map[o["id"]] = cur.fetchone()[0]
        for c in data["criteria"]:
            cur.execute(
                """INSERT INTO criteria
                     (opportunity_id, criterion_key, version, text, category,
                      check_type, source_quote, threshold)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (opportunity_id, criterion_key, version) DO NOTHING""",
                (id_map[c["opportunity_id"]], c["criterion_key"], c["version"], c["text"],
                 c["category"], c["check_type"], c["source_quote"],
                 json.dumps(c["threshold"]) if c["threshold"] else None),
            )
    print(f"Loaded {len(data['opportunities'])} opportunities, "
          f"{len(data['criteria'])} criteria.")


if __name__ == "__main__":
    main()
