"""Benefits-vertical ingestion: turn a public policy page (e.g. the USDA FNS
SNAP eligibility page) into an opportunity row the existing compiler and
engine can process unchanged.

This is the one-engine thesis made concrete: a benefit program is just
another rulebook. source='policy', and raw carries {"policy_text", "url"}
instead of a Grants.gov detail payload.
"""

from __future__ import annotations

import html
import json
import re

import requests


def fetch_policy_text(url: str) -> str:
    resp = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "claimable/0.1 (portfolio project; policy research)"},
    )
    resp.raise_for_status()
    text = resp.text
    text = re.sub(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ",
                  text, flags=re.S | re.I)
    # preserve table structure enough for the compiler to quote rows
    text = re.sub(r"</(tr|p|h[1-6]|li)>", "\n", text, flags=re.I)
    text = re.sub(r"</t[dh]>", " | ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def upsert_policy_opportunity(
    cur, *, number: str, title: str, agency_name: str, url: str, policy_text: str
) -> int:
    cur.execute(
        """INSERT INTO opportunities
             (source, source_id, number, title, agency_name, status, synopsis, raw)
           VALUES ('policy', %s, %s, %s, %s, 'posted', %s, %s)
           ON CONFLICT (source, source_id) DO UPDATE SET
             synopsis = EXCLUDED.synopsis,
             raw = EXCLUDED.raw,
             last_seen_at = now()
           RETURNING id""",
        (url, number, title, agency_name, policy_text[:8000],
         json.dumps({"policy_text": policy_text, "url": url})),
    )
    return cur.fetchone()[0]
