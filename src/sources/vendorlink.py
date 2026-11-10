"""
vendorlink.py — active bids from VendorLink (myvendorlink.com) agencies.

VendorLink's public bid board is https://www.myvendorlink.com/external/bids?a=<id>,
where <id> is VendorLink's numeric agency id (e.g. 91 = Polk County School
Board FL, 70 = Brevard County BOCC FL — the ids are the option values of the
page's own "Agency" dropdown). It's a server-rendered ASP.NET WebForms page,
no login.

A plain GET shows every bid (awarded, cancelled, ...) 20 per page, so we do
one normal form postback — the page's own "Search Bids" button — with the
Status filter set to "Active" (value 2) and 100 rows per page.

NOTE: the "View" button is a client-side postback with no per-bid URL, so
every opportunity links back to the agency's bid board — find the bid there
by its number (stored in set_aside, also shown in the title).
"""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_STATUS_ACTIVE = "2"
_PREFIX = "ctl00$RegionMiddle$"


class VendorLinkSource(Source):
    """Reads active bids from a list of VendorLink agencies."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "VendorLink")
        self.agencies = cfg.get("agencies", [])
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        return self.fetch_each(self.agencies, self._fetch_agency)

    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        agency_id = str(ag["agency_id"])
        agency_name = ag.get("agency", agency_id)
        state = ag.get("state", "")
        url = f"https://www.myvendorlink.com/external/bids?a={agency_id}"

        session = requests.Session()
        session.headers["User-Agent"] = _BROWSER_UA
        resp = session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        form = BeautifulSoup(resp.text, "html.parser").find("form")
        if form is None:
            raise RuntimeError("VendorLink: bid search form not found")

        data = {}
        for el in form.select("input"):
            name = el.get("name")
            if name and el.get("type") not in ("submit", "checkbox", "radio", "image"):
                data[name] = el.get("value", "")
        for sel in form.select("select"):
            opt = sel.find("option", selected=True)
            data[sel["name"]] = opt.get("value", "") if opt else ""
        data[_PREFIX + "ddlAgency"] = agency_id
        data[_PREFIX + "ddlStatus"] = _STATUS_ACTIVE
        data[_PREFIX + "rblSolicitationPageCount"] = "100"
        data[_PREFIX + "ddlSolicitationOrderBy"] = "SolicitationDueDateASC"
        data[_PREFIX + "btnSearch"] = "Search Bids"

        resp = session.post(url, data=data, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", id=lambda x: x and "grvSolicitations" in x)
        if table is None:
            raise RuntimeError("VendorLink: results grid not found")

        results: list[Opportunity] = []
        for row in table.find_all("tr"):
            opp = self._row_to_opp(row, agency_id, url, agency_name, state)
            if opp:
                results.append(opp)
        return results

    def _row_to_opp(self, row, agency_id: str, url: str, agency_name: str, state: str):
        cells = row.find_all("td", recursive=False)
        # Columns: [0] Agency, [1] Number, [2] Title, [3] Online Quote/Bid,
        # [4] Status, [5] Broadcast Date, [6] Mandatory Pre-Bid,
        # [7] Question End Date, [8] Due Date, [9] View.
        if len(cells) < 9:
            return None  # header / pager rows
        number = clean_text(cells[1].get_text())
        title = clean_text(cells[2].get_text())
        status = clean_text(cells[4].get_text())
        if not title or status.lower() != "active":
            return None
        row_agency = clean_text(cells[0].get_text()) or agency_name
        return Opportunity(
            id=f"vendorlink:{agency_id}:{number or title}",
            source_name=f"{self.name} — {agency_name}",
            title=f"{number} — {title}" if number else title,
            description=title,
            agency=row_agency,
            state=state,
            url=url,
            set_aside=number,
            posted_date=to_iso_date(clean_text(cells[5].get_text())),
            due_date=to_iso_date(clean_text(cells[8].get_text())),
        )
