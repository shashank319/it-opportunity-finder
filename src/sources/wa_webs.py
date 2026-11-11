"""
wa_webs.py — open solicitations from Washington's Electronic Business
Solution (WEBS), the State of Washington's bid portal run by DES.

WEBS' public "Bid Opportunities" page (BidCalendar.aspx on
pr-webs-vendor.des.wa.gov) lists every currently open solicitation — state
agencies, universities/colleges, plus some local governments that post
through WEBS — with no login. It's a server-rendered ASP.NET DataGrid,
25 rows per page; further pages are __doPostBack events that we replay with
the page's own __VIEWSTATE (requests.Session, no JS).

The grid rows have no agency column, so we use the page's own "Filter by
Government Organization" dropdown: one search per organization in the list,
tagging each row with that organization's name — but WEBS is SLOW
(~15 s per request when tested), so that mode (~50 requests, ~5 min) is
opt-in via `by_agency: true`. The default is a single unfiltered walk
(~6 requests, ~1.5 min) with agency left as "Washington State (WEBS)".

NOTE: the per-solicitation detail page (Search_BidDetails.aspx?ID=...) is
LOGIN-ONLY — it redirects to the vendor login. We still link to it (it's
the stable, specific URL; a registered vendor lands right on the bid after
logging in), and the id comes from its ID= parameter.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_BASE = "https://pr-webs-vendor.des.wa.gov/"
_CAL = _BASE + "BidCalendar.aspx"
_POSTBACK = re.compile(r"__doPostBack\('([^']+)'")
_ID = re.compile(r"[?&]ID=(\d+)", re.I)
_MAX_PAGES = 30  # per organization / walk; safety cap


def _form(soup) -> dict:
    return {i["name"]: i.get("value", "") for i in soup.select("input[type=hidden]") if i.get("name")}


class WaWebsSource(Source):
    """Reads open solicitations from the Washington WEBS public bid calendar."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Washington WEBS")
        self.timeout = int(cfg.get("timeout", 30))
        self.by_agency = bool(cfg.get("by_agency", False))

    def fetch(self) -> list[Opportunity]:
        s = requests.Session()
        s.headers["User-Agent"] = _BROWSER_UA
        resp = s.get(_CAL, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        sel = soup.find("select", attrs={"name": "ddlOrgNames"})
        if soup.find(id="DataGrid1") is None or sel is None:
            raise RuntimeError(f"WEBS bid calendar layout not recognized at {resp.url}")

        out: dict[str, Opportunity] = {}
        if not self.by_agency:
            for opp in self._walk(s, soup, "Washington State (WEBS)"):
                out[opp.id] = opp
            return list(out.values())

        orgs = [(o.get("value"), clean_text(o.get_text())) for o in sel.find_all("option")]
        for value, org_name in orgs:
            if not value or value == "0":  # "<All>"
                continue
            form = _form(soup)
            form["ddlOrgNames"] = value
            form["ImageButton1"] = "Search"
            r = s.post(_CAL, data=form, timeout=self.timeout)
            r.raise_for_status()
            org_soup = BeautifulSoup(r.text, "html.parser")
            for opp in self._walk(s, org_soup, org_name):
                out.setdefault(opp.id, opp)
        return list(out.values())

    def _walk(self, s, soup, agency: str):
        """Parse the current grid page, then follow the DataGrid pager."""
        page = 1
        for _ in range(_MAX_PAGES):
            grid = soup.find(id="DataGrid1")
            if grid is None:
                return  # no results for this organization
            for tr in grid.select("tr.grid_item, tr.grid_item_alt"):
                opp = self._row_to_opp(tr, agency)
                if opp:
                    yield opp
            target = self._pager_target(grid, page + 1)
            if not target:
                return
            form = _form(soup)
            form["__EVENTTARGET"] = target
            form["__EVENTARGUMENT"] = ""
            r = s.post(_CAL, data=form, timeout=self.timeout)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            page += 1

    @staticmethod
    def _pager_target(grid, next_page: int):
        rows = grid.find_all("tr")
        if not rows:
            return None
        pager = rows[-1]
        if "grid_item" in " ".join(pager.get("class", [])):
            return None  # no pager row => single page
        links = pager.find_all("a", href=True)
        for a in links:
            if clean_text(a.get_text()) == str(next_page):
                m = _POSTBACK.search(a["href"])
                return m.group(1) if m else None
        # DataGrid pagers with >10 pages show a trailing "..." for the next set.
        if links and clean_text(links[-1].get_text()) == "...":
            m = _POSTBACK.search(links[-1]["href"])
            return m.group(1) if m else None
        return None

    def _row_to_opp(self, tr, agency: str):
        cells = tr.find_all("td", recursive=False)
        # Columns: [0] close date (+ amendment date span), [1] title link,
        # "Ref#:..." and description, [2] contact.
        if len(cells) < 3:
            return None
        link = cells[1].find("a", href=_ID)
        if not link:
            return None
        bid_id = _ID.search(link["href"]).group(1)
        title = clean_text(link.get_text())
        if not title:
            return None

        # The text nodes directly in the cell (not inside the inner table)
        # are: title, "Ref#:xxx", description.
        direct = [clean_text(t) for t in cells[1].find_all(string=True, recursive=False)]
        direct = [t for t in direct if t]
        ref = ""
        desc_bits = []
        for t in direct:
            if t.startswith("Ref#:") and not ref:
                ref = clean_text(t[len("Ref#:"):])
            else:
                desc_bits.append(t)
        close_div = cells[0].find("div") or cells[0]
        close_txt = clean_text(close_div.find(string=True, recursive=False) or "")
        contact = clean_text(cells[2].get_text())

        desc = " — ".join(x for x in [title, " ".join(desc_bits),
                                      f"Contact: {contact}" if contact else ""] if x)
        return Opportunity(
            id=f"wawebs:{bid_id}",
            source_name=self.name,
            title=f"{title} ({ref})" if ref else title,
            description=desc,
            agency=agency,
            state="WA",
            url=urljoin(_BASE, link["href"]),
            set_aside=ref,
            due_date=to_iso_date(close_txt),
        )
