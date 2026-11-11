"""
florida_vbs.py — open advertisements from Florida's Vendor Bid System (VBS).

vendor.myfloridamarketplace.com/search/bids is the public (no login) VBS
search — Florida's official repository for competitive-procurement ads from
state agencies plus some universities, colleges, water management districts
and municipalities. It's an Angular app whose search form calls a JSON API:

    POST /mfmp/pub/search/bids        (body = the form's criteria + page)
    POST /mfmp/pub/search/bids/count

We send the page's own criteria with Status = OPEN. The API returns HTML
(the SPA shell) instead of JSON unless the request carries the same
`Accept: application/json` header the app sends.

"Agency Decision" ads (notices of intended award / decisions, not open
opportunities) are skipped by default; override with `exclude_types`.
"""

from __future__ import annotations

import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

BASE_URL = "https://vendor.myfloridamarketplace.com"
SEARCH_URL = f"{BASE_URL}/mfmp/pub/search/bids"
LISTING_URL = f"{BASE_URL}/search/bids"
_PAGE_SIZE = 100
_MAX_PAGES = 30

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class FloridaVBSSource(Source):
    """Reads open advertisements from Florida's Vendor Bid System."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Florida Vendor Bid System")
        self.state = cfg.get("state", "FL")
        self.url = cfg.get("url", LISTING_URL)
        self.timeout = int(cfg.get("timeout", 60))
        self.exclude_types = {t.lower() for t in cfg.get("exclude_types", ["Agency Decision"])}

    def fetch(self) -> list[Opportunity]:
        headers = {
            "User-Agent": _BROWSER_UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": self.url,
        }
        criteria = {
            "pageSize": str(_PAGE_SIZE), "type": [], "status": ["OPEN"], "agency": [],
            "adNumber": "", "agencyAdvertisementNumber": "", "title": "",
            "publishedDate": "", "openDate": "", "endDate": "", "commodityCodes": [],
            "intendsToParticipate": "", "assignee": "",
        }
        results: list[Opportunity] = []
        for page in range(1, _MAX_PAGES + 1):
            r = requests.post(SEARCH_URL, json=dict(criteria, page=page), headers=headers, timeout=self.timeout)
            r.raise_for_status()
            rows = r.json()   # raises if the API answered with HTML (format change)
            if not isinstance(rows, list):
                raise RuntimeError(f"Unexpected VBS response: {str(rows)[:200]}")
            for row in rows:
                opp = self._row_to_opp(row)
                if opp:
                    results.append(opp)
            if len(rows) < _PAGE_SIZE:
                break
        return results

    def _row_to_opp(self, row: dict):
        title = clean_text(row.get("title"))
        if not title:
            return None
        ad_type = clean_text(row.get("type"))
        if ad_type.lower() in self.exclude_types:
            return None
        if str(row.get("status") or "OPEN").upper() != "OPEN":
            return None
        ad_id = row.get("advertisementId")
        number = clean_text(row.get("agencyAdNumber"))
        org = row.get("organization") or {}
        return Opportunity(
            id=f"flvbs:{ad_id or number or title}",
            source_name=self.name,
            title=f"{number} — {title}" if number else title,
            description=f"{title} | Type: {ad_type}" if ad_type else title,
            agency=clean_text(row.get("agency") or org.get("name")),
            state=self.state,
            url=f"{BASE_URL}/search/bids/detail/{ad_id}" if ad_id else self.url,
            posted_date=to_iso_date(row.get("publishDate") or row.get("openDate")),
            due_date=to_iso_date(row.get("closeDate")),
        )
