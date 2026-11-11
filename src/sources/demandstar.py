"""
demandstar.py — open bids from DemandStar agency pages.

DemandStar's public agency page
(www.demandstar.com/app/agencies/<state>/<agency>/procurement-opportunities/<guid>/)
is a React SPA, but the bid list it shows comes from an unauthenticated
JSON endpoint:

    GET https://api.demandstar.com/contents/agency/search?id=<agency guid>

which returns {"total": N, "result": [ {bidId, bidName, bidIdentifier,
agency, broadCastDate, dueDate, city, state, status, statusType, ...} ]}.
No login, no token — the same call the public page makes.

The list includes recently closed bids too ("Under Evaluation", "Awarded",
...); we keep only statusType "AC" (status "Active"), i.e. still accepting
responses.

Config-driven: give it a list of agencies (guid + name + state). The guid is
the last path segment of the agency's DemandStar URL above.
Each bid links to DemandStar's public "limited" bid-details page.
"""

from __future__ import annotations

import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_API = "https://api.demandstar.com/contents/agency/search"
_DETAIL = "https://www.demandstar.com/app/limited/bids/{bid_id}/details"
_OPEN_STATUS_TYPES = {"AC"}  # "Active"


class DemandStarSource(Source):
    """Reads open (Active) bids from a list of DemandStar agency pages."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "DemandStar")
        self.agencies = cfg.get("agencies", [])
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        return self.fetch_each(self.agencies, self._fetch_agency)

    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        guid = ag["guid"]
        resp = requests.get(
            _API,
            params={"id": guid},
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "application/json",
                "Origin": "https://www.demandstar.com",
                "Referer": "https://www.demandstar.com/",
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("result") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError(f"DemandStar agency/search returned unexpected payload: {str(data)[:200]}")

        results: list[Opportunity] = []
        for rec in rows:
            opp = self._rec_to_opp(rec, ag)
            if opp:
                results.append(opp)
        return results

    def _rec_to_opp(self, rec: dict, ag: dict):
        if (rec.get("statusType") or "").upper() not in _OPEN_STATUS_TYPES:
            return None
        bid_id = rec.get("bidId")
        title = clean_text(rec.get("bidName"))
        if not bid_id or not title:
            return None
        agency_name = ag.get("agency") or clean_text(rec.get("agency"))
        ident = clean_text(rec.get("bidIdentifier"))
        where = ", ".join(x for x in (clean_text(rec.get("city")), clean_text(rec.get("state"))) if x)
        desc = " — ".join(x for x in (title, ident, where) if x)
        return Opportunity(
            id=f"demandstar:{bid_id}",
            source_name=f"{self.name} — {agency_name}",
            title=title,
            description=desc,
            agency=agency_name,
            state=ag.get("state") or clean_text(rec.get("state")),
            url=_DETAIL.format(bid_id=bid_id),
            set_aside=ident,
            posted_date=to_iso_date(rec.get("broadCastDate")),
            due_date=to_iso_date(rec.get("dueDate")),
        )
