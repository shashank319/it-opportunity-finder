"""
civicplus_bids.py — open bids from CivicPlus "Bids.aspx" municipal sites.

CivicPlus is a common CMS used by many small/medium US cities. Its stock
Bids module (a page usually at /bids.aspx or /Bids.aspx) is server-rendered
— a plain GET with a browser-like User-Agent returns the full listing, no
login or JS needed.

Config-driven: give it a list of agencies (url + name + state) pointing at
that agency's bids.aspx page. This is a generic parser for the SHARED
CivicPlus layout, so it can serve any city on this CMS just by adding a URL
— no per-agency code.

NOTE: not every CivicPlus site uses this exact bids.aspx module (some use a
different page entirely), so a new agency should be spot-checked once before
being added — if the page doesn't match this layout, fetch() simply returns
no rows for it rather than erroring.
"""

from __future__ import annotations

from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class CivicPlusBidsSource(Source):
    """Reads open bids from a list of CivicPlus Bids.aspx agency pages."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "CivicPlus Bids")
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
        url = ag["url"]
        agency_name = ag.get("agency", url)
        state = ag.get("state", "")
        resp = requests.get(url, headers={"User-Agent": _BROWSER_UA}, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        results: list[Opportunity] = []
        for row in soup.select("div.listItemsRow.bid"):
            opp = self._row_to_opp(row, url, agency_name, state)
            if opp:
                results.append(opp)
        return results

    def _row_to_opp(self, row, page_url: str, agency_name: str, state: str):
        title_a = row.select_one(".bidTitle a")
        if not title_a:
            return None
        title = clean_text(title_a.get_text())
        if not title:
            return None
        href = title_a.get("href", "")
        url = urljoin(page_url, href)

        status_div = row.select_one(".bidStatus")
        status, due = "", ""
        if status_div:
            value_divs = status_div.find_all("div", recursive=False)
            if len(value_divs) >= 2:
                spans = value_divs[1].find_all("span")
                if len(spans) >= 1:
                    status = clean_text(spans[0].get_text())
                if len(spans) >= 2:
                    due = clean_text(spans[1].get_text())

        # Only keep bids the page itself marks as open.
        if status and status.lower() != "open":
            return None

        return Opportunity(
            id=f"civicplus:{url}",
            source_name=f"{self.name} — {agency_name}",
            title=title,
            description=title,
            agency=agency_name,
            state=state,
            url=url,
            due_date=to_iso_date(due),
        )
