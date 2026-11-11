"""
buyspeed.py — open bids from Periscope BuySpeed ("/bso/") portals.

BuySpeed (Periscope Holdings) runs many statewide/county e-procurement
portals: OregonBuys, ARBuy, Nevada ePro, COMMBUYS, Illinois BidBuy,
PHLContracts, KnoxBuys, ... All of them share the same public, no-login bid
search at /bso/view/search/external/advancedSearchBid.xhtml?openBids=true.

That page is a JSF/PrimeFaces datatable. The first page of results is in the
initial HTML; later pages come from PrimeFaces' AJAX paging POST, which needs
the same session cookie plus the form's hidden fields (_csrf + ViewState).
The total row count and page size are in the table's widget config
(`rowCount:NNN`, `rows:25`); the server rejects any other page size.

Config-driven: give it a list of agencies (host + name + state), e.g.
oregonbuys.gov -> host "oregonbuys.gov". The "agency" shown on each record
is the row's Organization Name (the buying department / local government),
falling back to the portal's configured name.
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

_SEARCH_PATH = "/bso/view/search/external/advancedSearchBid.xhtml?openBids=true"
_FORM_ID = "bidSearchResultsForm"
_TABLE_ID = "bidSearchResultsForm:bidResultId"
_ROW_COUNT = re.compile(r"rowCount:(\d+)")
# Page size the table was rendered with; the server rejects any other value.
_PAGE_ROWS = re.compile(r"paginator:\{[^}]*?rows:(\d+)")
_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)


class BuySpeedSource(Source):
    """Reads open bids from a list of BuySpeed portals."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "BuySpeed")
        self.agencies = cfg.get("agencies", [])
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        return self.fetch_each(self.agencies, self._fetch_agency)

    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        host = ag["host"]
        portal_name = ag.get("agency", host)
        state = ag.get("state", "")
        base = f"https://{host}"
        listing_url = base + _SEARCH_PATH

        session = requests.Session()
        session.headers["User-Agent"] = _BROWSER_UA
        resp = session.get(listing_url, timeout=self.timeout)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        form = soup.find("form", id=_FORM_ID)
        if form is None:
            raise RuntimeError(f"{host}: bid search results form not found")
        m = _ROW_COUNT.search(resp.text)
        total = int(m.group(1)) if m else 0
        m = _PAGE_ROWS.search(resp.text)
        page_size = m.group(1) if m else "25"

        rows_html = [resp.text]
        hidden = {i["name"]: i.get("value", "") for i in form.find_all("input", type="hidden") if i.get("name")}
        first = len(_detail_rows(soup))
        while 0 < first < total:
            data = dict(hidden)
            data.update({
                "javax.faces.partial.ajax": "true",
                "javax.faces.source": _TABLE_ID,
                "javax.faces.partial.execute": _TABLE_ID,
                "javax.faces.partial.render": _TABLE_ID,
                f"{_TABLE_ID}_pagination": "true",
                f"{_TABLE_ID}_first": str(first),
                f"{_TABLE_ID}_rows": page_size,
                f"{_TABLE_ID}_encodeFeature": "true",
            })
            page = session.post(
                base + form["action"],
                data=data,
                headers={"Faces-Request": "partial/ajax", "X-Requested-With": "XMLHttpRequest", "Referer": listing_url},
                timeout=self.timeout,
            )
            page.raise_for_status()
            # The partial response is XML with the new <tr>s inside CDATA, which
            # html.parser would drop — unwrap it, then put the rows in a <table>
            # so BeautifulSoup parses them like the first page.
            page_html = "<table>" + "".join(_CDATA.findall(page.text)) + "</table>"
            got = len(_detail_rows(BeautifulSoup(page_html, "html.parser")))
            if got == 0:
                break
            rows_html.append(page_html)
            first += got

        columns = _column_index(soup)
        results: dict[str, Opportunity] = {}
        for html in rows_html:
            for row in _detail_rows(BeautifulSoup(html, "html.parser")):
                opp = self._row_to_opp(row, columns, base, host, portal_name, state)
                if opp:
                    results[opp.id] = opp
        return list(results.values())

    def _row_to_opp(self, row, columns: dict, base: str, host: str, portal_name: str, state: str):
        cells = row.find_all("td")

        def col(name: str) -> str:
            i = columns.get(name)
            return clean_text(cells[i].get_text()) if i is not None and i < len(cells) else ""

        link = row.find("a", href=lambda h: h and "bidDetail" in h)
        bid_number = clean_text(link.get_text()) if link else col("Bid Solicitation #")
        title = col("Description")
        if not title:
            return None
        agency = col("Organization Name") or portal_name

        return Opportunity(
            id=f"buyspeed:{host}:{bid_number or title}",
            source_name=f"{self.name} — {portal_name}",
            title=title,
            description=title,
            agency=agency,
            state=state,
            url=base + link["href"] if link else base + _SEARCH_PATH,
            set_aside=bid_number,
            due_date=to_iso_date(col("Bid Opening Date")),
        )


def _detail_rows(soup) -> list:
    """Result rows = <tr>s containing a bid-detail link."""
    rows = []
    for a in soup.find_all("a", href=lambda h: h and "bidDetail" in h):
        tr = a.find_parent("tr")
        if tr is not None and tr not in rows:
            rows.append(tr)
    return rows


def _column_index(soup) -> dict:
    """Map header text -> column index (first occurrence; headers repeat for
    responsive layouts, e.g. "Bid Solicitation #" appears twice)."""
    rows = _detail_rows(soup)
    table = rows[0].find_parent("table") if rows else None
    thead = table.find("thead") if table else None
    index: dict[str, int] = {}
    if thead:
        for i, th in enumerate(thead.find_all("th")):
            index.setdefault(clean_text(th.get_text()), i)
    return index
