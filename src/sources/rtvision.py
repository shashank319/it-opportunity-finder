"""
rtvision.py — out-for-bid contracts from RTVision Connex.

Unlike the other agency-portal sources, RTVision Connex is a SHARED
statewide feed (observed covering Minnesota public-works bids) — one public,
unauthenticated endpoint returns every posting agency's open contracts
together, each record already tagged with its own agency name:
    https://connex.rtvision.com/api/contract/list-out-for-bid?limit=<n>

So this adapter fetches the shared feed ONCE and keeps only the records
whose `agency` field matches one of the configured agency names (substring,
case-insensitive) — add an agency by adding its name as it appears in that
field (e.g. "City of Rochester, MN", "Olmsted County, MN").

NOTE: this feed is public-works/construction-bid focused (bridges, culverts,
grading...) going by what it returns, so most records will correctly get
filtered out downstream as non-IT — that's expected, not a bug. It's still
worth keeping wired up in case an agency posts an IT-flavored contract here.
"""

from __future__ import annotations

import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

API_URL = "https://connex.rtvision.com/api/contract/list-out-for-bid"
LISTING_URL = "https://connex.rtvision.com/contracts/out-for-bid?quick-filter=all"
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class RtVisionSource(Source):
    """Reads the shared RTVision Connex feed, filtered to configured agencies."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "RTVision Connex")
        self.agencies = cfg.get("agencies", [])
        self.limit = int(cfg.get("limit", 1000))
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        resp = requests.get(
            API_URL, params={"limit": self.limit},
            headers={"User-Agent": _BROWSER_UA, "Accept": "application/json"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        rows = resp.json().get("data", []) or []

        # Match each row's `agency` field (case-insensitive substring) against
        # the configured agency_match list, so one shared feed can serve
        # several configured agencies without a separate request each.
        wanted = [
            (ag["agency_match"].lower(), ag.get("agency", ag["agency_match"]), ag.get("state", ""))
            for ag in self.agencies if ag.get("enabled", True)
        ]

        results: list[Opportunity] = []
        for row in rows:
            row_agency = (row.get("agency") or "").lower()
            for match, display_name, state in wanted:
                if match in row_agency:
                    opp = self._row_to_opp(row, display_name, state)
                    if opp:
                        results.append(opp)
                    break
        return results

    def _row_to_opp(self, row: dict, agency_name: str, state: str):
        title = clean_text(row.get("name"))
        if not title:
            return None
        cid = row.get("id")
        return Opportunity(
            id=f"rtvision:{cid or title}",
            source_name=f"{self.name} — {agency_name}",
            title=title,
            description=clean_text(row.get("workTypes")) or title,
            agency=agency_name,
            state=state,
            url=LISTING_URL,
            set_aside=clean_text(row.get("number")),
            due_date=to_iso_date(row.get("bidOpening")),
        )
