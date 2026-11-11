"""
georgia_gpr.py — open bids from the Georgia Procurement Registry (GPR).

The GPR (ssl.doas.state.ga.us/gpr) is Georgia's statewide public bid
registry — state agencies plus cities, counties, K-12 and other local
entities. Its public search page (no login) fills a server-side DataTables
grid from a JSON endpoint:

    POST /gpr/eventSearch   (form-encoded DataTables params + search fields)

We send exactly what the page sends with Event Status = OPEN and page
through the results. Each row links to the public eventDetails page.
"""

from __future__ import annotations

from urllib.parse import quote

import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

BASE_URL = "https://ssl.doas.state.ga.us/gpr"
LISTING_URL = f"{BASE_URL}/index"
_PAGE_SIZE = 100
_MAX_PAGES = 30
_COLUMNS = 8

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class GeorgiaGPRSource(Source):
    """Reads open bids from the Georgia Procurement Registry."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Georgia Procurement Registry")
        self.state = cfg.get("state", "GA")
        self.url = cfg.get("url", LISTING_URL)
        self.timeout = int(cfg.get("timeout", 60))

    def fetch(self) -> list[Opportunity]:
        s = requests.Session()
        s.headers["User-Agent"] = _BROWSER_UA
        s.get(self.url, timeout=self.timeout).raise_for_status()   # session cookie

        results: list[Opportunity] = []
        for page in range(_MAX_PAGES):
            r = s.post(
                f"{BASE_URL}/eventSearch",
                data=self._form(page),
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": self.url},
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
            rows = data.get("data")
            if rows is None:
                raise RuntimeError(f"GPR search error: {data.get('message')}")
            for row in rows:
                opp = self._row_to_opp(row)
                if opp:
                    results.append(opp)
            if len(rows) < _PAGE_SIZE or (page + 1) * _PAGE_SIZE >= int(data.get("recordsFiltered") or 0):
                break
        return results

    def _form(self, page: int) -> dict:
        d = {
            "draw": page + 1, "start": page * _PAGE_SIZE, "length": _PAGE_SIZE,
            "search[value]": "", "search[regex]": "false",
            "order[0][column]": "5", "order[0][dir]": "asc",
        }
        for i in range(_COLUMNS):
            d.update({
                f"columns[{i}][data]": str(i), f"columns[{i}][name]": "",
                f"columns[{i}][searchable]": "true", f"columns[{i}][orderable]": "true",
                f"columns[{i}][search][value]": "", f"columns[{i}][search][regex]": "false",
            })
        d.update({
            "responseType": "ALL", "eventStatus": "OPEN", "eventIdTitle": "",
            "govType": "ALL", "govEntity": "", "catType": "ALL", "eventProcessType": "ALL",
            "dateRangeType": "", "rangeStartDate": "", "rangeEndDate": "",
            "isReset": "false", "persisted": "false", "refreshSearchData": "true",
        })
        return d

    def _row_to_opp(self, row: dict):
        title = clean_text(row.get("title"))
        number = clean_text(row.get("esourceNumber"))
        if not title:
            return None
        if str(row.get("status") or "open").lower() != "open":
            return None
        key = row.get("esourceNumberKey") or number
        url = (
            f"{BASE_URL}/eventDetails?eSourceNumber={quote(str(key))}"
            f"&sourceSystemType={quote(str(row.get('sourceId') or ''))}"
        )
        return Opportunity(
            id=f"gagpr:{number or title}",
            source_name=self.name,
            title=f"{number} — {title}" if number else title,
            description=clean_text(row.get("esourceDescription")) or title,
            agency=clean_text(row.get("agencyName")),
            state=self.state,
            url=url,
            posted_date=to_iso_date(row.get("postingDate")),
            due_date=to_iso_date(row.get("closingDate")),
        )
