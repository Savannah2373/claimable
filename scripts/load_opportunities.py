#!/usr/bin/env python3
"""Load fetched JSONL opportunity files into Postgres.

Usage:
    python scripts/load_opportunities.py                 # loads every file in data/raw/
    python scripts/load_opportunities.py data/raw/x.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.db import connect, upsert_opportunity


def main() -> None:
    paths = [Path(p) for p in sys.argv[1:]] or sorted(Path("data/raw").glob("*.jsonl"))
    if not paths:
        sys.exit("No JSONL files found. Run scripts/fetch_opportunities.py first.")

    inserted = updated = 0
    with connect() as conn, conn.cursor() as cur:
        for path in paths:
            with path.open() as f:
                for line in f:
                    if upsert_opportunity(cur, json.loads(line)):
                        inserted += 1
                    else:
                        updated += 1

    print(f"Loaded {len(paths)} file(s): {inserted} new, {updated} updated.")


if __name__ == "__main__":
    main()
