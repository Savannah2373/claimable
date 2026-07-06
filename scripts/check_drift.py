#!/usr/bin/env python3
"""Rule-drift monitor. For every opportunity with compiled criteria, re-fetch
the live source (Grants.gov detail or policy URL), rebuild the exact text the
compiler would see, and compare its hash against the snapshot taken at compile
time. On drift: mark that opportunity's analyses stale and tell the operator
to recompile — stale verdicts must never be served as current.

Run on a schedule (cron / CI nightly). Exit code 1 when drift is found, so a
scheduled job can alert.

Usage:
    python scripts/check_drift.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.compiler import build_source_text
from claimable.db import connect
from claimable.ingestion.benefits import fetch_policy_text
from claimable.ingestion.grants_gov import GrantsGovClient


def _live_source_text(client: GrantsGovClient, row: dict) -> str | None:
    raw = row["raw"] or {}
    if "policy_text" in raw:
        try:
            fresh = fetch_policy_text(raw["url"])
        except Exception as exc:  # noqa: BLE001 — unreachable source ≠ drift
            print(f"  ! could not re-fetch {raw.get('url')}: {exc}")
            return None
        return build_source_text({**row, "raw": {"policy_text": fresh, "url": raw["url"]}})
    try:
        detail = client.fetch_detail(row["source_id"])
    except Exception as exc:  # noqa: BLE001
        print(f"  ! could not re-fetch detail for {row['number']}: {exc}")
        return None
    return build_source_text({**row, "raw": {"detail": detail}})


def main() -> None:
    client = GrantsGovClient()
    drifted: list[str] = []

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT o.id, o.source_id, o.number, o.title, o.raw,
                      (SELECT d.content_sha256 FROM documents d
                       WHERE d.opportunity_id = o.id AND d.kind = 'source_snapshot'
                       ORDER BY d.id DESC LIMIT 1) AS snapshot
               FROM opportunities o
               JOIN criteria c ON c.opportunity_id = o.id AND c.superseded_at IS NULL
               ORDER BY o.number""",
        )
        rows = [
            {"id": r[0], "source_id": r[1], "number": r[2], "title": r[3],
             "raw": r[4], "snapshot": r[5]}
            for r in cur.fetchall()
        ]

        for row in rows:
            if not row["snapshot"]:
                print(f"  ~ {row['number']}: no snapshot (compiled before drift "
                      f"monitoring existed) — recompile to baseline")
                continue
            live = _live_source_text(client, row)
            if live is None:
                continue
            live_hash = hashlib.sha256(live.encode()).hexdigest()
            if live_hash == row["snapshot"]:
                print(f"  ✓ {row['number']}: unchanged")
            else:
                drifted.append(row["number"])
                cur.execute(
                    "UPDATE analyses SET status = 'stale' WHERE opportunity_id = %s",
                    (row["id"],),
                )
                print(f"  ✗ {row['number']}: SOURCE CHANGED — analyses marked stale; "
                      f"recompile with: python scripts/compile_criteria.py {row['number']}")

    if drifted:
        print(f"\nDRIFT DETECTED in {len(drifted)} source(s): {', '.join(drifted)}")
        sys.exit(1)
    print("\nNo drift detected.")


if __name__ == "__main__":
    main()
