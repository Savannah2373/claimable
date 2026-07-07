"""GrantConnect (grants.gov.au) client — Australia's central grants portal.

GrantConnect publishes every Commonwealth grant opportunity but offers no
public API: the Current Grant Opportunity List and GO detail pages are
server-rendered HTML (and the server rejects non-browser user agents unless
a session cookie is held). This client keeps a cookie session and parses the
portal's uniform `list-desc` label/value markup.

Detail pages carry a real Eligibility section plus award amounts, so each GO
is normalized into the benefits-vertical `{"policy_text", "url"}` raw shape —
the criteria compiler consumes it with zero changes (see
compiler.build_source_text).
"""

from __future__ import annotations

import html
import re
import time
from typing import Any, Iterator

import requests

from claimable.ingestion.grants_gov import Opportunity

BASE_URL = "https://www.grants.gov.au"
_TIMEOUT = 30
_USER_AGENT = "Mozilla/5.0 (compatible; claimable/0.1; +https://github.com/Savannah2373/claimable)"

# <div class="list-desc"> <span>LABEL:</span> <div class="list-desc-inner">VALUE</div> </div>
_FIELD_RE = re.compile(
    r'<div class="list-desc">\s*<span>(?P<label>.*?)</span>\s*'
    r'<div class="list-desc-inner">(?P<value>.*?)</div>\s*</div>',
    re.S,
)
_LIST_ROW_RE = re.compile(
    r'href="/Go/Show\?GoUuid=(?P<uuid>[0-9a-f-]{36})"[^>]*\stitle="(?P<number>GO\d+)"'
)
_TITLE_RE = re.compile(r'class="font20"[^>]*>(?P<title>.*?)</p>', re.S)
_AU_DATE_RE = re.compile(r"(\d{1,2})-([A-Z][a-z]{2})-(\d{4})")
# explicit map instead of strptime %b, which is locale-dependent and would
# silently null every date on a non-English LC_TIME host
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}

# Deterministic section order — GrantConnect labels mapped to compiler headings.
# (Ordering matters: source text is hashed for drift detection.)
_POLICY_SECTIONS = [
    ("Description", "DESCRIPTION"),
    ("Eligibility", "ELIGIBILITY"),
    ("Total Amount Available (AUD)", "TOTAL AMOUNT AVAILABLE (AUD)"),
    ("Estimated Grant Value (AUD)", "ESTIMATED GRANT VALUE (AUD)"),
    ("Location", "LOCATION"),
    ("Selection Process", "SELECTION PROCESS"),
    ("Primary Category", "PRIMARY CATEGORY"),
    ("Instructions for Application Submission", "HOW TO APPLY"),
]


def _text(fragment: str) -> str:
    """HTML fragment → readable text, keeping list items as '- ' bullets."""
    fragment = re.sub(r"(?s)<li[^>]*>", "\n- ", fragment)
    fragment = re.sub(r"(?s)</(p|ul|ol|li)>|<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?s)<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in fragment.splitlines()]
    return "\n".join(line for line in lines if line)


def _parse_au_date(value: str | None) -> str | None:
    """'28-Jul-2026 5:00 pm (ACT Local Time)' → '2026-07-28' (ISO)."""
    match = _AU_DATE_RE.search(value or "")
    if not match:
        return None
    day, month, year = match.groups()
    if month not in _MONTHS:
        return None
    return f"{year}-{_MONTHS[month]:02d}-{int(day):02d}"


class GrantConnectClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = _USER_AGENT

    def _get(self, path: str, **params: Any) -> str:
        resp = self.session.get(f"{BASE_URL}{path}", params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.text

    def list_current(self, max_results: int = 50) -> Iterator[tuple[str, str]]:
        """Yield (uuid, GO number) for current opportunities, newest first."""
        seen: set[str] = set()
        page = 1
        while len(seen) < max_results:
            rows = _LIST_ROW_RE.findall(self._get("/Go/List", page=page))
            new = [(u, n) for u, n in dict(rows).items() if u not in seen]
            if not new:
                return
            for uuid, number in new:
                seen.add(uuid)
                yield uuid, number
                if len(seen) >= max_results:
                    return
            page += 1
            time.sleep(0.5)  # stay polite to a scraped portal

    def fetch_detail(self, uuid: str, number_hint: str | None = None) -> Opportunity:
        """Fetch one GO detail page and normalize it for the opportunities table.

        `number_hint` is the GO number already parsed from the list page; it
        backstops the detail page's "GO ID" field so a template change there
        can't silently strip numbers (number-less rows are unreachable by the
        compile scripts' `WHERE number = %s` lookups).
        """
        page = self._get("/Go/Show", GoUuid=uuid)
        fields = {
            _text(m["label"]).rstrip(":"): m["value"]
            for m in _FIELD_RE.finditer(page)
        }
        title_match = _TITLE_RE.search(page)
        number = _text(fields.get("GO ID", "")) or number_hint
        url = f"{BASE_URL}/Go/Show?GoUuid={uuid}"

        sections = [
            f"## {heading}\n{_text(fields[label])}"
            for label, heading in _POLICY_SECTIONS
            if fields.get(label)
        ]
        if not sections:
            # never store an empty rulebook: a compile against a bare header
            # would produce criteria with no citable source text
            raise ValueError(
                f"GO page {url} yielded no recognizable sections — "
                "markup may have changed; adapter needs updating"
            )
        status = "closed" if "Closed Grant Opportunity" in page else "posted"

        return Opportunity(
            source="grantconnect",
            source_id=uuid,
            number=number or None,
            title=_text(title_match["title"]) if title_match else "(untitled)",
            agency_code=None,  # GrantConnect has no agency-code concept — always None
            agency_name=_text(fields.get("Agency", "")) or None,
            status=status,
            open_date=_parse_au_date(fields.get("Publish Date")),
            close_date=_parse_au_date(fields.get("Close Date & Time")),
            synopsis=_text(fields.get("Description", "")) or None,
            raw={"policy_text": "\n\n".join(sections), "url": url},
        )
