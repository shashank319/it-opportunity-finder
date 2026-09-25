"""
ionwave.py — open bids from Ionwave agency portals.

Ionwave hosts a per-agency subdomain portal (e.g. leegov.ionwave.net). The
open-bids list at /SourcingEvents.aspx?SourceType=1 is a server-rendered
Telerik grid — the data is in the initial HTML response, no login and no
JS/API call needed, just a plain GET.

Config-driven: give it a list of agencies (tenant + name + state). Find the
tenant from the agency's Ionwave URL, e.g. leegov.ionwave.net -> tenant
"leegov".

NOTE: the grid has no per-row detail link in the HTML (it's a client-side
postback), so every opportunity links back to the agency's listing page —
find the specific bid there by its Bid Number (shown in the title).
"""

from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

# Ionwave's close-date column ends with a timezone abbreviation in
# parentheses, e.g. "9/22/2026 02:30:00 PM (ET)", which trips up dateutil's
# parser (it returns '' rather than raising). Strip it before parsing.
_TZ_SUFFIX = re.compile(r"\s*\([A-Za-z]{2,5}\)\s*$")

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class IonwaveSource(Source):
    """Reads open bids from a list of Ionwave agency portals."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Ionwave")
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
        tenant = ag["tenant"]
        agency_name = ag.get("agency", tenant)
        state = ag.get("state", "")
        listing_url = f"https://{tenant}.ionwave.net/SourcingEvents.aspx?SourceType=1"
        resp = requests.get(listing_url, headers={"User-Agent": _BROWSER_UA}, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        results: list[Opportunity] = []
        for row in soup.select("tr.rgRow, tr.rgAltRow"):
            opp = self._row_to_opp(row, tenant, listing_url, agency_name, state)
            if opp:
                results.append(opp)
        return results

    def _row_to_opp(self, row, tenant: str, listing_url: str, agency_name: str, state: str):
        cells = row.find_all("td")
        # Columns: [0] view icon, [1] Bid Number, [2] Bid Title, [3] Bid Type,
        # [4] Organization, [5] Bid Issue Date, [6] Bid Close Date/Time.
        if len(cells) < 7:
            return None
        bid_number = clean_text(cells[1].get_text())
        title = clean_text(cells[2].get_text())
        if not title:
            return None

        return Opportunity(
            id=f"ionwave:{tenant}:{bid_number or title}",
            source_name=f"{self.name} — {agency_name}",
            title=title,
            description=title,
            agency=agency_name,
            state=state,
            url=listing_url,
            set_aside=bid_number,
            posted_date=to_iso_date(clean_text(cells[5].get_text())),
            due_date=to_iso_date(_TZ_SUFFIX.sub("", clean_text(cells[6].get_text()))),
        )
