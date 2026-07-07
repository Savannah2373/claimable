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
from functools import cache
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.compiler import build_source_text
from claimable.db import connect
from claimable.ingestion.benefits import fetch_policy_text
from claimable.ingestion.grantconnect import GrantConnectClient
from claimable.ingestion.grants_gov import GrantsGovClient


@cache
def _grants_gov() -> GrantsGovClient:
    return GrantsGovClient()


@cache
def _grantconnect() -> GrantConnectClient:
    return GrantConnectClient()


def _refetch_grants_gov(row: dict) -> dict[str, Any]:
    return {"detail": _grants_gov().fetch_detail(row["source_id"])}


def _refetch_policy(row: dict) -> dict[str, Any]:
    url = row["raw"]["url"]
    return {"policy_text": fetch_policy_text(url), "url": url}


def _refetch_grantconnect(row: dict) -> dict[str, Any]:
    # grantconnect policy_text is assembled from the GO page's labeled
    # sections, so re-fetch through the same adapter — not the page scraper
    return _grantconnect().fetch_detail(row["source_id"]).raw


# one re-fetcher per opportunities.source — a new ingestion source registers
# here or its rows are reported (not silently mis-fetched via a wrong default)
_REFETCHERS = {
    "grants.gov": _refetch_grants_gov,
    "policy": _refetch_policy,
    "grantconnect": _refetch_grantconnect,
}


def _live_source_text(row: dict) -> str | None:
    refetch = _REFETCHERS.get(row["source"])
    if refetch is None:
        print(f"  ! {row['number']}: no re-fetcher registered for "
              f"source {row['source']!r} — skipping")
        return None
    try:
        fresh_raw = refetch(row)
    except Exception as exc:  # noqa: BLE001 — unreachable source ≠ drift
        print(f"  ! could not re-fetch {row['number']}: {exc}")
        return None
    return build_source_text({**row, "raw": fresh_raw})


def main() -> None:
    drifted: list[str] = []

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT o.id, o.source, o.source_id, o.number, o.title, o.raw,
                      (SELECT d.content_sha256 FROM documents d
                       WHERE d.opportunity_id = o.id AND d.kind = 'source_snapshot'
                       ORDER BY d.id DESC LIMIT 1) AS snapshot
               FROM opportunities o
               JOIN criteria c ON c.opportunity_id = o.id AND c.superseded_at IS NULL
               ORDER BY o.number""",
        )
        rows = [
            {"id": r[0], "source": r[1], "source_id": r[2], "number": r[3],
             "title": r[4], "raw": r[5], "snapshot": r[6]}
            for r in cur.fetchall()
        ]

        for row in rows:
            if not row["snapshot"]:
                print(f"  ~ {row['number']}: no snapshot (compiled before drift "
                      f"monitoring existed) — recompile to baseline")
                continue
            live = _live_source_text(row)
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
