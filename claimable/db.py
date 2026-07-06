"""Database access. One connection helper + upsert for opportunities."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import psycopg
from dotenv import load_dotenv

load_dotenv()

DEFAULT_DATABASE_URL = "postgresql://claimable:claimable@localhost:5433/claimable"


def connect() -> psycopg.Connection:
    return psycopg.connect(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))


def _parse_date(value: str | None) -> str | None:
    """Grants.gov dates arrive as MM/DD/YYYY; Postgres wants ISO."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def upsert_opportunity(cur: psycopg.Cursor, opp: dict[str, Any]) -> bool:
    """Insert or refresh one opportunity dict (shape of ingestion.Opportunity).

    Returns True if a new row was inserted, False if an existing one was updated.
    """
    cur.execute(
        """
        INSERT INTO opportunities
            (source, source_id, number, title, agency_code, agency_name,
             status, open_date, close_date, synopsis, raw)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source, source_id) DO UPDATE SET
            title        = EXCLUDED.title,
            status       = EXCLUDED.status,
            open_date    = EXCLUDED.open_date,
            close_date   = EXCLUDED.close_date,
            synopsis     = COALESCE(EXCLUDED.synopsis, opportunities.synopsis),
            raw          = EXCLUDED.raw,
            last_seen_at = now()
        RETURNING (xmax = 0) AS inserted
        """,
        (
            opp["source"],
            opp["source_id"],
            opp.get("number"),
            opp["title"],
            opp.get("agency_code"),
            opp.get("agency_name"),
            opp.get("status"),
            _parse_date(opp.get("open_date")),
            _parse_date(opp.get("close_date")),
            opp.get("synopsis"),
            json.dumps(opp.get("raw", {})),
        ),
    )
    return cur.fetchone()[0]
