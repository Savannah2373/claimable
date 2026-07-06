#!/usr/bin/env python3
"""Seed synthetic test personas (no real PII — ever).

The first persona is deliberately shaped to exercise all three verdicts
against the two compiled HRSA grants: it should be broadly eligible for the
rural research program (HRSA-26-050) but is missing its requested funding
amount (→ needs_info), and it is not a state government (→ not_met on the
State Offices program, HRSA-26-065).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimable.db import connect

PROFILES = [
    {
        "kind": "organization",
        "name": "Appalachian Rural Health Data Collaborative",
        "attrs": {
            "entity_type": "501(c)(3) nonprofit organization",
            "state": "Ohio",
            "country": "United States",
            "mission": (
                "Rapid data analysis and research on rural health access and "
                "outcomes across 32 Appalachian Ohio counties"
            ),
            "staff_count": 12,
            "annual_budget_usd": 1_400_000,
            "is_state_government": False,
            "designated_by_governor": False,
            "has_active_sam_registration": True,
            "prior_federal_awards": ["HRSA rural outreach subaward (2024)"],
            # requested_funding_usd deliberately absent → should trigger needs_info
        },
    },
    {
        # The fully-eligible showcase for HRSA-26-065, with the deterministic
        # facts populated so threshold checks run in code, not in the LLM.
        "kind": "organization",
        "name": "Ohio Department of Health — State Office of Rural Health",
        "attrs": {
            "entity_type": "state government agency",
            "state": "Ohio",
            "country": "United States",
            "mission": (
                "The state-designated focal point for rural health in Ohio, "
                "linking rural communities with state and federal resources"
            ),
            "is_state_government": True,
            "operates_state_office_of_rural_health": True,
            "designated_by_governor": True,
            "can_provide_cost_sharing": True,
            "requested_funding_usd": 220_000,
            "has_active_sam_registration": True,
        },
    },
]


def main() -> None:
    with connect() as conn, conn.cursor() as cur:
        for p in PROFILES:
            cur.execute("SELECT id FROM profiles WHERE name = %s", (p["name"],))
            if cur.fetchone():
                print(f"exists:  {p['name']}")
                continue
            cur.execute(
                "INSERT INTO profiles (kind, name, attrs) VALUES (%s, %s, %s)",
                (p["kind"], p["name"], json.dumps(p["attrs"])),
            )
            print(f"seeded:  {p['name']}")


if __name__ == "__main__":
    main()
