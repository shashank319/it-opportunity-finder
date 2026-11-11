"""
pa_emarketplace.py — current solicitations from Pennsylvania's eMarketplace.

emarketplace.state.pa.us/Search.aspx is a public (no login) ASP.NET WebForms
page. A plain GET shows "View Current Records" 10 rows at a time; we do the
same postback the "No. of records per page" dropdown does (value 32767 =
"ALL") to get every current solicitation in one server-rendered grid.

Covers Commonwealth agencies, PASSHE universities, DGS construction and
some local/state-related bodies. Rows link to the public Solicitations.aspx
detail page. Status "Created"/"Extended" rows are kept (not yet open /
deadline moved); anything closed, awarded or cancelled is dropped.
"""

from __future__ import annotations

from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

LISTING_URL = "https://www.emarketplace.state.pa.us/Search.aspx"
_ALL_ROWS = "32767"
_CLOSED_WORDS = ("closed", "award", "cancel")

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class PAEMarketplaceSource(Source):
    """Reads current solicitations from Pennsylvania eMarketplace."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Pennsylvania eMarketplace")
        self.state = cfg.get("state", "PA")
        self.url = cfg.get("url", LISTING_URL)
        self.timeout = int(cfg.get("timeout", 120))

    def fetch(self) -> list[Opportunity]:
        s = requests.Session()
        s.headers["User-Agent"] = _BROWSER_UA
        first = s.get(self.url, timeout=self.timeout)
        first.raise_for_status()
        soup = BeautifulSoup(first.text, "html.parser")

        form = {i["name"]: i.get("value", "") for i in soup.select("input[type=hidden]") if i.get("name")}
        form.update({
            "__EVENTTARGET": "ctl00$MainBody$ddlRows",
            "__EVENTARGUMENT": "",
            "ctl00$MainBody$ddlRows": _ALL_ROWS,
            "ctl00$MainBody$ddlAgency": "0",
            "ctl00$MainBody$ddlCounty": "0",
            "ctl00$MainBody$ddlTypes": "",
            "ctl00$MainBody$rdoArch": "0",       # current (not archived) records
            "ctl00$MainBody$txtBidNo": "",
            "ctl00$MainBody$txtBidTitle": "",
        })
        resp = s.post(self.url, data=form, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        grid = soup.select_one("#ctl00_MainBody_grdResults")
        if grid is None:
            raise RuntimeError("PA eMarketplace results grid not found")

        results: list[Opportunity] = []
        for row in grid.select("tr.GridItem, tr.GridAltItem"):
            opp = self._row_to_opp(row)
            if opp:
                results.append(opp)
        return results

    def _row_to_opp(self, row):
        # Columns: [0] Solicitation #, [1] Type, [2] Title, [3] Description,
        # [4] Agency, [5] County, [6] Amended, [7] Start, [8] Due,
        # [9] Bid Opening, [10] Status, [11] Contact.
        cells = row.find_all("td", recursive=False)
        if len(cells) < 11:
            return None
        status = clean_text(cells[10].get_text())
        if any(w in status.lower() for w in _CLOSED_WORDS):
            return None
        link = cells[0].find("a")
        number = clean_text(cells[0].get_text())
        title_span = cells[2].find("span", title=True)
        title = clean_text(title_span["title"] if title_span else cells[2].get_text())
        if not title:
            return None
        full_desc = cells[3].find("div")
        desc = clean_text(full_desc.get_text() if full_desc else cells[3].get_text())
        sol_type = clean_text(cells[1].get_text())
        county = clean_text(cells[5].get_text())
        return Opportunity(
            id=f"paemkt:{number or title}",
            source_name=self.name,
            title=f"{number} — {title}" if number else title,
            description=" | ".join(x for x in (desc or title, f"Type: {sol_type}" if sol_type else "",
                                                f"County: {county}" if county else "") if x),
            agency=clean_text(cells[4].get_text()),
            state=self.state,
            url=urljoin(self.url, link["href"]) if link and link.get("href") else self.url,
            posted_date=to_iso_date(clean_text(cells[7].get_text())),
            due_date=to_iso_date(clean_text(cells[8].get_text())),
        )
