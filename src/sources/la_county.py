"""
la_county.py — open solicitations from Los Angeles County, CA (LACoBids).

A one-off adapter: the county's eCAPS "Open Solicitations" list at
camisvr.co.la.ca.us/LACoBids/BidLookUp/OpenBidList is a server-rendered
ASP.NET MVC table (tbody#searchTbl1), public, no login. It is paginated
("Page 1 of 22" at 10 per page), but the pager links are plain GETs with a
PageSize query parameter that the server honors beyond the 10/20/50/100 the
dropdown offers — so one request with PageSize=1000 returns every open bid.
We still follow "Page X of Y" in case the list ever outgrows that.

Columns: [0] Solicitation Number, [1] Title (+ commodity line),
[2] Type, [3] Department, [4] Close Date ("MM/DD/YYYY hh:mm AM" or
"Continuous" for open-continuous solicitations such as qualified-vendor
pools — those are kept, with no due date).

NOTE: the real detail page (BidLookUp/BidDetail) is POST-only (a GET errors),
so each record links to the list filtered by its solicitation number
(OpenBidList?TextSearch=<number>), which shows just that one bid and is a
shareable GET URL.
"""

from __future__ import annotations

import re
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

LISTING_URL = "https://camisvr.co.la.ca.us/LACoBids/BidLookUp/OpenBidList"
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_PAGE_OF = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)", re.I)


class LACountySource(Source):
    """Reads open solicitations from LA County's LACoBids open-bid list."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Los Angeles County, CA")
        self.state = cfg.get("state", "CA")
        self.url = cfg.get("url", LISTING_URL)
        self.page_size = int(cfg.get("page_size", 1000))
        self.timeout = int(cfg.get("timeout", 60))

    def fetch(self) -> list[Opportunity]:
        results: list[Opportunity] = []
        seen: set[str] = set()
        page, last_page = 1, 1
        while page <= last_page:
            params = {
                "page": page,
                "TextSearch": "|||",
                "FieldSort": "BidCloseDate",
                "DirectionSort": "Asc",
                "PageSize": self.page_size,
            }
            resp = requests.get(self.url, params=params,
                                headers={"User-Agent": _BROWSER_UA}, timeout=self.timeout)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            tbody = soup.select_one("tbody#searchTbl1")
            if tbody is None:
                # Layout changed or an Incapsula/bot page came back — fail loudly.
                raise RuntimeError("LA County: open-bid table (tbody#searchTbl1) not found")

            m = _PAGE_OF.search(soup.get_text(" "))
            last_page = int(m.group(2)) if m else page

            for row in tbody.find_all("tr", recursive=False):
                opp = self._row_to_opp(row)
                if opp and opp.id not in seen:
                    seen.add(opp.id)
                    results.append(opp)
            page += 1
        return results

    def _row_to_opp(self, row):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 5:
            return None
        num_a = cells[0].find("a")
        sol_no = clean_text(num_a.get_text()) if num_a else clean_text(cells[0].get_text())
        if not sol_no:
            return None

        title_el = cells[1].select_one("label[id^=LabelTitle]")
        title = clean_text(title_el.get_text()) if title_el else ""
        if not title:
            return None
        # The full (non-truncated) commodity text sits in the collapsed panel.
        # (div#collapseN; div#collapseTitleN holds the title instead.)
        comm_el = None
        for panel in cells[1].select("div[id^=collapse]"):
            if not panel.get("id", "").startswith("collapseTitle"):
                comm_el = panel.find("label")
                break
        comm_el = comm_el or cells[1].select_one("label[id^=LabelComm]")
        commodity = clean_text(comm_el.get_text()).replace("Commodity:", "").strip() if comm_el else ""
        bid_type = clean_text(cells[2].get_text())
        department = clean_text(cells[3].get_text())
        close_text = clean_text(cells[4].get_text())
        due = "" if close_text.lower().startswith("continuous") else to_iso_date(close_text)

        desc_parts = [title]
        if commodity:
            desc_parts.append(f"Commodity: {commodity}")
        if bid_type:
            desc_parts.append(f"Type: {bid_type}")
        if not due and close_text:
            desc_parts.append(f"Close: {close_text}")

        return Opportunity(
            id=f"lacounty:{sol_no}",
            source_name=self.name,
            title=f"{sol_no} — {title}",
            description=" | ".join(desc_parts),
            agency=f"Los Angeles County — {department}" if department else "Los Angeles County",
            state=self.state,
            url=f"{self.url}?TextSearch={quote(sol_no)}",
            due_date=due,
        )
