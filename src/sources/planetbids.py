"""
planetbids.py — open bids from PlanetBids agency portals.

PlanetBids is a procurement platform used by many (mostly California) cities,
counties, and special districts. Each agency has its own "portal" identified by
a numeric portalId (called `cid` in the API). The portal page is a JavaScript
app, but it loads its bid list from a PUBLIC JSON API that needs no login —
only a browser-like `Referer`/`User-Agent` (an anti-direct-access check).

So this adapter is config-driven: give it a list of agencies (portalId + name +
state) and it pulls each agency's OPEN bids. Add more agencies by adding config
entries — no code change. Find a portalId from an agency's PlanetBids URL:
    https://vendors.planetbids.com/portal/<portalId>/bo/bo-search

NOTE: this hits an undocumented public endpoint. If PlanetBids ever changes it,
this source will simply log an error and be skipped — it can't break the run.
"""

from __future__ import annotations

import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

API_URL = "https://api-external.prod.planetbids.com/papi/bids"

# A browser-like User-Agent + Referer/Origin are required or the API returns 403.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class PlanetBidsSource(Source):
    """Reads OPEN bids from a list of PlanetBids agency portals."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "PlanetBids")
        self.agencies = cfg.get("agencies", [])
        # Which PlanetBids "stage" counts as open/biddable.
        self.open_stages = set(cfg.get("open_stages", ["Bidding"]))
        # PlanetBids caps per_page at 30; larger values return HTTP 400.
        self.per_page = min(int(cfg.get("per_page", 30)), 30)
        self.max_pages = int(cfg.get("max_pages", 4))
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        out: list[Opportunity] = []
        for ag in self.agencies:
            if not ag.get("enabled", True):
                continue
            out.extend(self._fetch_agency(ag))
        return out

    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        portal_id = str(ag["portal_id"])
        agency_name = ag.get("agency", f"PlanetBids portal {portal_id}")
        state = ag.get("state", "")
        headers = {
            "User-Agent": _BROWSER_UA,
            "Accept": "application/vnd.api+json",
            "Referer": f"https://vendors.planetbids.com/portal/{portal_id}/bo/bo-search",
            "Origin": "https://vendors.planetbids.com",
        }

        results: list[Opportunity] = []
        for page in range(1, self.max_pages + 1):
            params = {
                "bid_type_id": 0, "cid": portal_id, "dept_id": 0,
                "due_date_from": "", "due_date_to": "", "keyword": "",
                "page": page, "per_page": self.per_page,
                "sort_by": "", "sort_order": -1, "stage_id": 0,
            }
            resp = requests.get(API_URL, params=params, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            rows = data.get("data", []) or []
            for row in rows:
                opp = self._row_to_opp(row, portal_id, agency_name, state)
                if opp:
                    results.append(opp)
            # Stop once we've read the last page.
            total_pages = (data.get("meta") or {}).get("totalPages", 1)
            if page >= total_pages or not rows:
                break
        return results

    def _row_to_opp(self, row: dict, portal_id: str, agency_name: str, state: str):
        attr = row.get("attributes", {}) or {}
        # Only keep OPEN (biddable) solicitations.
        if attr.get("stageStr") not in self.open_stages:
            return None
        title = clean_text(attr.get("title"))
        if not title:
            return None
        bid_id = attr.get("bidId") or row.get("id")
        return Opportunity(
            id=f"planetbids:{portal_id}:{bid_id}",
            source_name=f"{self.name} — {agency_name}",
            title=title,
            description=clean_text(attr.get("invitationNum") or title),
            agency=agency_name,
            state=state,
            url=f"https://vendors.planetbids.com/portal/{portal_id}/bo/bo-detail/{bid_id}",
            # PlanetBids tags bids with NIGP commodity codes — capture them so
            # they show and are filterable on the dashboard.
            psc=clean_text(attr.get("categoryIds")),
            posted_date=to_iso_date(attr.get("issueDate")),
            due_date=to_iso_date(attr.get("bidDueDate")),
        )
