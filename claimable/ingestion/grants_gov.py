"""Grants.gov Search2 API client.

Public API, no key required. Docs: https://www.grants.gov/api/api-guide

Two endpoints matter for ingestion:
  - search2:          keyword/filter search over opportunities (paginated)
  - fetchOpportunity: full detail for one opportunity, including synopsis text
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import requests

BASE_URL = "https://api.grants.gov/v1/api"
_TIMEOUT = 30
_PAGE_SIZE = 25


@dataclass
class Opportunity:
    """Normalized shape matching the `opportunities` table (db/schema.sql)."""

    source: str
    source_id: str
    number: str | None
    title: str
    agency_code: str | None
    agency_name: str | None
    status: str | None
    open_date: str | None
    close_date: str | None
    synopsis: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_search_hit(cls, hit: dict[str, Any]) -> "Opportunity":
        return cls(
            source="grants.gov",
            source_id=str(hit["id"]),
            number=hit.get("number"),
            title=hit.get("title", "(untitled)"),
            agency_code=hit.get("agencyCode"),
            agency_name=hit.get("agency"),
            status=hit.get("oppStatus"),
            open_date=hit.get("openDate"),
            close_date=hit.get("closeDate"),
            synopsis=None,  # filled by fetch_detail()
            raw=hit,
        )


class GrantsGovClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = "claimable/0.1 (portfolio project)"

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self.session.post(f"{BASE_URL}/{endpoint}", json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
        body = resp.json()
        if body.get("errorcode") not in (0, None):
            raise RuntimeError(f"{endpoint} error {body.get('errorcode')}: {body.get('msg')}")
        return body.get("data", {})

    def search(
        self,
        keyword: str = "",
        statuses: str = "posted",
        max_results: int = 100,
    ) -> Iterator[Opportunity]:
        """Yield opportunities matching `keyword`, newest postings first."""
        fetched = 0
        start = 0
        while fetched < max_results:
            data = self._post(
                "search2",
                {
                    "keyword": keyword,
                    "oppStatuses": statuses,
                    "rows": min(_PAGE_SIZE, max_results - fetched),
                    "startRecordNum": start,
                },
            )
            hits = data.get("oppHits", [])
            if not hits:
                return
            for hit in hits:
                yield Opportunity.from_search_hit(hit)
                fetched += 1
                if fetched >= max_results:
                    return
            start += len(hits)
            time.sleep(0.3)  # stay polite to a free public API

    def fetch_detail(self, opportunity_id: str) -> dict[str, Any]:
        """Full record for one opportunity, including synopsis and attachment list."""
        return self._post("fetchOpportunity", {"opportunityId": int(opportunity_id)})

    def enrich_with_synopsis(self, opp: Opportunity) -> Opportunity:
        detail = self.fetch_detail(opp.source_id)
        synopsis = (detail.get("synopsis") or {}).get("synopsisDesc")
        opp.synopsis = synopsis
        opp.raw = {"search_hit": opp.raw, "detail": detail}
        return opp
