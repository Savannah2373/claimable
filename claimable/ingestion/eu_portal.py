"""EU Funding & Tenders Portal (SEDIA) client — the EU's central grants portal.

Two public, keyless endpoints:
  - search:       POST api.tech.ec.europa.eu/search-api/prod/rest/search
                  (apiKey=SEDIA is a public constant, not a credential)
  - topicDetails: GET  ec.europa.eu/info/funding-tenders/opportunities/data/
                  topicDetails/{identifier}.json

The search layer is messy — multi-cutoff topics carry stale deadlines and the
status facet disagrees with the topic page — so this client treats the
topicDetails JSON as authoritative: an opportunity is only yielded when its
action status is Open, and close_date is the nearest future cutoff.

Each topic's description + conditions text is normalized into the shared
`{"policy_text", "url"}` raw shape, so the criteria compiler and engine
consume EU rulebooks with zero changes.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from typing import Any, Iterator

import requests

from claimable.ingestion.grants_gov import Opportunity
from claimable.ingestion.html_text import html_to_text as _text

SEARCH_URL = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
DETAIL_URL = "https://ec.europa.eu/info/funding-tenders/opportunities/data/topicDetails/{ident}.json"
PORTAL_URL = ("https://ec.europa.eu/info/funding-tenders/opportunities/portal/"
              "screen/opportunities/topic-details/{ident}")
_TIMEOUT = 30
_PAGE_SIZE = 50
_USER_AGENT = "Mozilla/5.0 (compatible; claimable/0.1; +https://github.com/Savannah2373/claimable)"

_STATUS_OPEN = "31094502"
_TYPE_GRANT_TOPIC = "1"

# Deterministic section order — topicDetails fields mapped to compiler headings.
# (Ordering matters: source text is hashed for drift detection.)
_POLICY_SECTIONS = [
    ("description", "DESCRIPTION"),
    ("conditions", "ELIGIBILITY AND CONDITIONS"),
]


def _ms_to_date(ms: Any) -> str | None:
    """Epoch milliseconds (int or str) → ISO date, UTC."""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


class EUPortalClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = _USER_AGENT

    def _search_page(self, page: int) -> list[dict[str, Any]]:
        query = {"bool": {"must": [
            {"terms": {"type": [_TYPE_GRANT_TOPIC]}},
            {"terms": {"status": [_STATUS_OPEN]}},
        ]}}
        resp = self.session.post(
            SEARCH_URL,
            params={"apiKey": "SEDIA", "text": "***",
                    "pageSize": _PAGE_SIZE, "pageNumber": page},
            files={
                "query": (None, json.dumps(query), "application/json"),
                "sort": (None, '{"field":"deadlineDate","order":"DESC"}',
                         "application/json"),
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])

    def list_open_topics(self, max_results: int = 50) -> Iterator[str]:
        """Yield unique topic identifiers the search layer believes are open.
        (Sorted by farthest deadline first — currently-open calls cluster
        there; fetch_detail is the authority on true openness.)"""
        seen: set[str] = set()
        page = 1
        while len(seen) < max_results:
            hits = self._search_page(page)
            new = []
            for hit in hits:
                # multi-cutoff topics appear once per cutoff in the same page,
                # so dedupe against everything seen so far, page included
                idents = hit.get("metadata", {}).get("identifier") or []
                if idents and idents[0] not in seen:
                    seen.add(idents[0])
                    new.append(idents[0])
                    if len(seen) >= max_results:
                        break
            if not new:
                return
            yield from new
            page += 1
            time.sleep(0.3)  # stay polite to a free public API

    def fetch_detail(self, identifier: str) -> Opportunity | None:
        """Fetch one topic's detail JSON; None unless its action status is
        Open (the search facet routinely disagrees with the topic page)."""
        resp = self.session.get(
            DETAIL_URL.format(ident=identifier.lower()), timeout=_TIMEOUT
        )
        resp.raise_for_status()
        topic = resp.json().get("TopicDetails", {})
        actions = topic.get("actions") or [{}]
        action = actions[0]
        if (action.get("status") or {}).get("abbreviation") != "Open":
            return None

        today = date.today().isoformat()
        deadlines = sorted(
            d for d in (_ms_to_date(ms) for ms in action.get("deadlineDates") or [])
            if d is not None
        )
        future = [d for d in deadlines if d >= today]
        close_date = future[0] if future else (deadlines[-1] if deadlines else None)

        sections = [
            f"## {heading}\n{_text(topic[field])}"
            for field, heading in _POLICY_SECTIONS
            if topic.get(field)
        ]
        if not sections:
            # never store an empty rulebook: a compile against a bare header
            # would produce criteria with no citable source text
            raise ValueError(
                f"topic {identifier} has no description or conditions text — "
                "portal payload may have changed; adapter needs updating"
            )

        programme = topic.get("frameworkProgramme") or {}
        return Opportunity(
            source="eu_portal",
            source_id=identifier,
            number=identifier,
            title=topic.get("title") or "(untitled)",
            agency_code=(programme.get("abbreviation") or None),
            agency_name=(programme.get("description") or None),
            status="posted",
            open_date=_ms_to_date(action.get("plannedOpeningDate")),
            close_date=close_date,
            synopsis=_text(topic.get("description") or "") or None,
            raw={"policy_text": "\n\n".join(sections),
                 "url": PORTAL_URL.format(ident=identifier)},
        )
