"""
biddingo.py — open tenders from Biddingo (biddingousa.com) agency portals.

Biddingo's portal page is an Angular app, but it loads its listing from a
public, unauthenticated REST API discovered by watching the portal page's own
network calls:
  1. GET  /restapi/setting/noauthorize/buyerlandingpageinfo/<slug>  -> orgId
  2. POST /restapi/bidding/list/noauthorize/1/<orgId>               -> bid list
Both endpoints are marked "noauthorize" and work with a plain request — no
browser/session needed.

Config-driven: give it a list of agencies (slug + name + state). Find the slug
from the agency's Biddingo URL, e.g. biddingo.com/santaclaracounty -> slug
"santaclaracounty".
"""

from __future__ import annotations

import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

API_BASE = "https://api.biddingousa.com/restapi"
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class BiddingoSource(Source):
    """Reads open tenders from a list of Biddingo agency portals."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Biddingo")
        self.agencies = cfg.get("agencies", [])
        self.max_rows = int(cfg.get("max_rows", 100))
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        out: list[Opportunity] = []
        for ag in self.agencies:
            if not ag.get("enabled", True):
                continue
            out.extend(self._fetch_agency(ag))
        return out

    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        slug = ag["slug"]
        agency_name = ag.get("agency", slug)
        state = ag.get("state", "")
        headers = {
            "User-Agent": _BROWSER_UA,
            "Accept": "application/json",
            "Referer": "https://biddingousa.com/",
            "Content-Type": "application/json;charset=UTF-8",
        }

        info_resp = requests.get(
            f"{API_BASE}/setting/noauthorize/buyerlandingpageinfo/{slug}",
            headers=headers, timeout=self.timeout,
        )
        info_resp.raise_for_status()
        org_id = (info_resp.json().get("buyerLandingpageInfoBean") or {}).get("orgId")
        if not org_id:
            raise RuntimeError(f"Biddingo: no orgId found for slug '{slug}'")

        body = {
            "startResult": 0, "maxRow": self.max_rows,
            "filterRegionId": [], "filterCategoryId": [], "filterStatus": [],
            "closingDateStart": "", "closingDateEnd": "",
            "postedDateStart": "", "postedDateEnd": "",
            "selectedRegionId": [], "showOnlyResearchBid": False,
            "searchString": "", "startDate": "", "endDate": "",
            "searchType": "closing", "selectedChildOrgIdList": [], "sortType": "",
        }
        list_resp = requests.post(
            f"{API_BASE}/bidding/list/noauthorize/1/{org_id}",
            json=body, headers=headers, timeout=self.timeout,
        )
        list_resp.raise_for_status()
        bids = list_resp.json().get("bidInfoList", []) or []

        results: list[Opportunity] = []
        for b in bids:
            opp = self._bid_to_opp(b, slug, org_id, agency_name, state)
            if opp:
                results.append(opp)
        return results

    def _bid_to_opp(self, b: dict, slug: str, org_id, agency_name: str, state: str):
        # Only keep bids actually open for bidding (the list endpoint can also
        # return recently-closed ones depending on sort/filters).
        if "open" not in (b.get("bidStatus") or "").lower():
            return None
        title = clean_text(b.get("tenderName"))
        if not title:
            return None
        tender_id = b.get("tenderId") or b.get("biddingoTenderId")
        return Opportunity(
            id=f"biddingo:{slug}:{tender_id}",
            source_name=f"{self.name} — {agency_name}",
            title=title,
            description=title,
            agency=clean_text(b.get("publishedBy")) or agency_name,
            state=state,
            url=f"https://biddingousa.com/{slug}/bid/1/{org_id}/{tender_id}/verification",
            set_aside=clean_text(b.get("tenderNumber")),
            posted_date=to_iso_date(b.get("publishedDate")),
            due_date=to_iso_date(b.get("tenderClosingDate")),
        )
