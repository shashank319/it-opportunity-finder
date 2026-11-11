"""
jaggaer_public.py — open bids from JAGGAER (formerly SciQuest) public event pages.

Many universities and governments publish their open sourcing events on a
JAGGAER "PublicEvent" router page, e.g.
    https://bids.sciquest.com/apps/Router/PublicEvent?CustomerOrg=DASIowa
    https://bids01.jaggaer.com/apps/Router/PublicEvent?CustomerOrg=FSU
The page is server-rendered HTML (no login, no JS/API call needed). Each
event is a table row with a title link, a short description, and label/value
pairs for Open, Close, Type, Number and Contact.

Paging: the page accepts `PageSize` and `PageNum` query params as a plain GET.
We ask for 200 per page and follow "Page N of M" if there's more.

Config-driven: give it a list of agencies (org + host + name + state). Find
`org` from the CustomerOrg= query param and `host` from the URL's hostname
(bids.sciquest.com, bids01.jaggaer.com, ...; defaults to bids.sciquest.com).

NOTE on links: each event's "ViewSourcingEvent" link carries a per-request
AuthToken (it changes on every page load and leads to the supplier sign-in
flow), so we link back to the agency's public listing page instead — find the
event there by its Number (kept in `set_aside`, like the Ionwave adapter).
"""

from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_PAGE_SIZE = 200
_MAX_PAGES = 10
_PAGE_OF = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)")
# Close/Open values look like "3/1/2027, 3:00 PM EST" — drop the trailing
# timezone abbreviation so dateutil parses it cleanly.
_TZ_SUFFIX = re.compile(r"\s+[A-Z]{2,5}\s*$")


class JaggaerPublicSource(Source):
    """Reads open events from a list of JAGGAER PublicEvent pages."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "JAGGAER")
        self.agencies = cfg.get("agencies", [])
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        return self.fetch_each(self.agencies, self._fetch_agency)

    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        org = ag["org"]
        host = ag.get("host", "bids.sciquest.com")
        agency_name = ag.get("agency", org)
        state = ag.get("state", "")
        listing_url = f"https://{host}/apps/Router/PublicEvent?CustomerOrg={org}"

        results: list[Opportunity] = []
        seen: set[str] = set()
        page = 1
        while page <= _MAX_PAGES:
            resp = requests.get(
                listing_url,
                params={"PageSize": _PAGE_SIZE, "PageNum": page},
                headers={"User-Agent": _BROWSER_UA},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            if "PublicEvent" not in resp.url:
                raise RuntimeError(f"redirected away from public page: {resp.url}")
            soup = BeautifulSoup(resp.text, "html.parser")
            for row in soup.find_all("tr"):
                opp = self._row_to_opp(row, org, listing_url, agency_name, state)
                if opp and opp.id not in seen:
                    seen.add(opp.id)
                    results.append(opp)
            m = _PAGE_OF.search(soup.get_text(" "))
            if not m or int(m.group(1)) >= int(m.group(2)):
                break
            page += 1
        return results

    def _row_to_opp(self, row, org, listing_url, agency_name, state):
        link = row.find("a", class_="btn-link-header")
        if not link:
            return None
        title = clean_text(link.get_text())
        if not title:
            return None

        # label -> value pairs ("Open", "Close", "Type", "Number", "Contact").
        fields: dict[str, str] = {}
        for pair in row.select("div.table-row-layout"):
            name = pair.select_one("div.data-row-name")
            value = pair.select_one("div.data-row-content")
            if name and value:
                fields[clean_text(name.get_text()).lower()] = clean_text(value.get_text(" "))

        desc_div = row.select_one("div.label-mini")
        desc = clean_text(desc_div.get_text(" ")) if desc_div else ""
        desc = desc.lstrip("•").strip()
        number = fields.get("number", "")
        ev_type = fields.get("type", "")

        return Opportunity(
            id=f"jaggaer:{org}:{number or title}",
            source_name=f"{self.name} — {agency_name}",
            title=title,
            description=" — ".join(p for p in (ev_type, desc) if p) or title,
            agency=agency_name,
            state=state,
            url=listing_url,
            set_aside=number,
            posted_date=to_iso_date(_TZ_SUFFIX.sub("", fields.get("open", ""))),
            due_date=to_iso_date(_TZ_SUFFIX.sub("", fields.get("close", ""))),
        )
