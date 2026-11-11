"""
camden_county_nj.py — open procurements from Camden County, NJ.

procurements.camdencounty.com lists current bids/RFPs/quotes for Camden
County and related entities (CCMUA, Camden County College, Technical
Schools) in a public, server-rendered, paginated table (?page=N, 25/page):
#, Title, Entity, Description (+ "View Advertisement" PDF), Type, Status,
Opening Date.

NOTE: the per-procurement pages (/procurements/<id>) redirect to a vendor
login, so we link to the public legal-advertisement PDF when the row has
one, else back to the listing. Titles/descriptions in the table are
truncated by the site ("...").
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

LISTING_URL = "https://procurements.camdencounty.com/"
_ID = re.compile(r"/procurements/(\d+)")
_CLOSED_WORDS = ("closed", "award", "cancel", "rescind")
_MAX_PAGES = 20

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class CamdenCountyNJSource(Source):
    """Reads open procurements from Camden County NJ's procurement site."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Camden County, NJ")
        self.state = cfg.get("state", "NJ")
        self.url = cfg.get("url", LISTING_URL)
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        results: list[Opportunity] = []
        seen: set[str] = set()
        for page in range(1, _MAX_PAGES + 1):
            resp = requests.get(
                self.url, params={"page": page, "search": ""},
                headers={"User-Agent": _BROWSER_UA}, timeout=self.timeout,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = [r for r in soup.select("table tr") if r.find("td")]
            new = 0
            for row in rows:
                opp = self._row_to_opp(row)
                if opp and opp.id not in seen:
                    seen.add(opp.id)
                    results.append(opp)
                    new += 1
            if not rows or not new:
                break
        return results

    def _row_to_opp(self, row):
        cells = row.find_all("td")
        if len(cells) < 7:
            return None
        status = clean_text(cells[5].get_text())
        if any(w in status.lower() for w in _CLOSED_WORDS):
            return None
        link = cells[1].find("a", href=_ID)
        title = clean_text(cells[1].get_text())
        if not title:
            return None
        proc_id = _ID.search(link["href"]).group(1) if link else ""
        number = clean_text(cells[0].get_text())
        if number in ("--", "-"):
            number = ""
        ad = cells[3].find("a", href=True)
        desc_cell = BeautifulSoup(str(cells[3]), "html.parser")
        for a in desc_cell.find_all("a"):
            a.decompose()
        desc = clean_text(desc_cell.get_text(" "))
        sol_type = clean_text(cells[4].get_text())
        # The visible Opening Date is the LAST td (an older one is commented out).
        opening = clean_text(cells[-1].get_text())
        entity = clean_text(cells[2].get_text())
        return Opportunity(
            id=f"camdennj:{proc_id or number or title}",
            source_name=self.name,
            title=f"{number} — {title}" if number else title,
            description=" | ".join(x for x in (desc or title, f"Type: {sol_type}" if sol_type else "",
                                                f"Status: {status}" if status else "") if x),
            agency=f"{entity}, NJ" if entity and "NJ" not in entity else (entity or "Camden County, NJ"),
            state=self.state,
            url=urljoin(self.url, ad["href"]) if ad else self.url,
            due_date=to_iso_date(opening),
        )
