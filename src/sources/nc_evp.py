"""
nc_evp.py — open solicitations from North Carolina's eProcurement portal (eVP).

evp.nc.gov is a Microsoft Power Pages site. The public /solicitations/ page
(no login) renders an "entity list" grid whose rows are loaded by the
page's own JavaScript from a JSON endpoint:

    POST /_services/entity-grid-data.json/<grid id>

The request body carries the grid's signed view configuration (embedded in
the page as base64 `data-view-layouts`) and the page's anti-forgery token
(served to every anonymous visitor at /_layout/tokenhtml). We replay exactly
what the browser does, with the page's own "Open" status filter
(metaFilter "3=0" — the same checkbox the page ticks by default).

Covers state agencies, universities, community colleges and many local
governments in NC (the Department column becomes the agency).
"""

from __future__ import annotations

import base64
import json
import re

import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

BASE_URL = "https://evp.nc.gov"
LISTING_URL = f"{BASE_URL}/solicitations/?status=0"
DETAIL_URL = f"{BASE_URL}/solicitations/details/?id="
_OPEN_FILTER = "3=0"      # filter group 3 (Status) = 0 (Open)
_PAGE_SIZE = 50
_MAX_PAGES = 40

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class NCEVPSource(Source):
    """Reads open solicitations from North Carolina eVP (evp.nc.gov)."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "North Carolina eVP")
        self.state = cfg.get("state", "NC")
        self.url = cfg.get("url", LISTING_URL)
        self.timeout = int(cfg.get("timeout", 60))

    def fetch(self) -> list[Opportunity]:
        s = requests.Session()
        s.headers["User-Agent"] = _BROWSER_UA

        page = s.get(self.url, timeout=self.timeout)
        page.raise_for_status()
        layouts = re.search(r'data-view-layouts="([^"]+)"', page.text)
        grid = re.search(r'data-get-url="(/_services/entity-grid-data\.json/[^"]+)"', page.text)
        if not layouts or not grid:
            raise RuntimeError("eVP page layout changed: grid config not found")
        layout = json.loads(base64.b64decode(layouts.group(1)))[0]

        tok = s.get(f"{BASE_URL}/_layout/tokenhtml", timeout=self.timeout)
        tok.raise_for_status()
        m = re.search(r'value="([^"]+)"', tok.text)
        if not m:
            raise RuntimeError("eVP anti-forgery token not found")
        headers = {"__RequestVerificationToken": m.group(1), "X-Requested-With": "XMLHttpRequest"}

        body = {
            "base64SecureConfiguration": layout["Base64SecureConfiguration"],
            "sortExpression": layout.get("SortExpression", ""),
            "search": "",
            "page": 1,
            "pageSize": _PAGE_SIZE,
            "pagingCookie": "",
            "filter": None,
            "metaFilter": _OPEN_FILTER,
            "nlSearchFilter": "",
            "timezoneOffset": 0,
            "customParameters": [],
        }

        results: list[Opportunity] = []
        for page_no in range(1, _MAX_PAGES + 1):
            body["page"] = page_no
            r = s.post(BASE_URL + grid.group(1), json=body, headers=headers, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            for rec in data.get("Records", []):
                opp = self._rec_to_opp(rec)
                if opp:
                    results.append(opp)
            if not data.get("MoreRecords"):
                break
            body["pagingCookie"] = data.get("PagingCookie") or ""
        return results

    def _rec_to_opp(self, rec: dict):
        a = {}
        for attr in rec.get("Attributes", []):
            a[attr.get("Name")] = attr.get("FormattedValue") or attr.get("DisplayValue") or attr.get("Value")
        title = clean_text(a.get("evp_name"))
        if not title:
            return None
        status = str(a.get("statuscode") or "")
        if status and status.lower() != "open":
            return None
        number = clean_text(a.get("evp_solicitationnbr"))
        rec_id = rec.get("Id") or a.get("evp_solicitationid") or ""
        return Opportunity(
            id=f"ncevp:{rec_id or number}",
            source_name=self.name,
            title=f"{number} — {title}" if number else title,
            description=clean_text(a.get("evp_description")) or title,
            agency=clean_text(a.get("owningbusinessunit")),
            state=self.state,
            url=DETAIL_URL + rec_id if rec_id else self.url,
            posted_date=to_iso_date(a.get("evp_posteddate")),
            due_date=to_iso_date(a.get("evp_opendate")),
        )
