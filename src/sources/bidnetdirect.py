"""
bidnetdirect.py — open solicitations from BidNet Direct agency portals.

BidNet Direct hosts a public "Bid Opportunities and RFPs" page per agency at
https://www.bidnetdirect.com/<path>, e.g. .../california/cityofsantaclara.
The bid table is server-rendered into the initial HTML — no JS/API call
needed — so a plain GET with a browser-like User-Agent returns it directly.
(A default headless-browser fingerprint gets a 403 here; a plain requests GET
does not, which is why this adapter doesn't need Playwright.)

Config-driven: give it a list of agencies (path + name + state). Find the path
from the agency's BidNet Direct URL, e.g. bidnetdirect.com/california/
cityofsantaclara -> path "california/cityofsantaclara".
"""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

BASE_URL = "https://www.bidnetdirect.com"
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class BidNetDirectSource(Source):
    """Reads open solicitations from a list of BidNet Direct agency portals."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "BidNet Direct")
        self.agencies = cfg.get("agencies", [])
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        out: list[Opportunity] = []
        for ag in self.agencies:
            if not ag.get("enabled", True):
                continue
            out.extend(self._fetch_agency(ag))
        return out

    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        path = ag["path"].strip("/")
        agency_name = ag.get("agency", path)
        state = ag.get("state", "")
        resp = requests.get(
            f"{BASE_URL}/{path}", headers={"User-Agent": _BROWSER_UA}, timeout=self.timeout
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        results: list[Opportunity] = []
        for row in soup.select("tr.mets-table-row"):
            opp = self._row_to_opp(row, path, agency_name, state)
            if opp:
                results.append(opp)
        return results

    def _row_to_opp(self, row, path: str, agency_name: str, state: str):
        title_a = row.select_one(".sol-title a")
        if not title_a:
            return None
        title = clean_text(title_a.get_text())
        if not title:
            return None
        href = title_a.get("href", "")
        url = href if href.startswith("http") else f"{BASE_URL}{href}"

        sol_num_el = row.select_one(".sol-num")
        sol_num = clean_text(sol_num_el.get_text()) if sol_num_el else ""

        posted_el = row.select_one(".sol-publication-date .date-value")
        due_el = row.select_one(".sol-closing-date .date-value")
        posted = clean_text(posted_el.get_text()) if posted_el else ""
        due = clean_text(due_el.get_text()) if due_el else ""

        return Opportunity(
            id=f"bidnetdirect:{path}:{sol_num or title}",
            source_name=f"{self.name} — {agency_name}",
            title=title,
            description=title,
            agency=agency_name,
            state=state,
            url=url,
            # Reference/solicitation number, for display only — not treated as
            # a classification code (pipeline.py only code-matches naics/psc).
            set_aside=sol_num,
            posted_date=to_iso_date(posted),
            due_date=to_iso_date(due),
        )
