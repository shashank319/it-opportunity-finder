"""
ivalua.py — open solicitations from Ivalua-hosted public "View Solicitations" pages.

Many agencies run their supplier portal on Ivalua (e.g. San Diego County
BuyNet at sdbuynet.sandiegocounty.gov). Each tenant exposes a PUBLIC, no-login
listing at:

    <base_url>/page.aspx/en/rfp/request_browse_public

How the page works:
  * A plain GET returns server-rendered HTML. The grid is
    <table id="body_x_grid_grd">; each solicitation is a
    <tr data-object-type="rfp" data-id="<internal id>"> row. Column order can
    differ per tenant, so we map columns by the header <th> ids
    (…colBpmCodeCalculated = RFx #, …colLabel = RFx Name, …) with the header
    label text as a fallback.
  * The grid shows 15 rows per page. Paging is a standard ASP.NET postback —
    exactly what the "Next page" button does in a browser: POST the page's own
    form back with __EVENTTARGET=<grid unique id> and __EVENTARGUMENT="Page|N".
    We keep a requests.Session so the ASP.NET session cookie from the GET is
    sent with those postbacks. (Note: the GridView's postback target is its
    ClientID "body_x_grid_grd", not the colon form.)
  * The listing is the tenant's whole RFx history (San Diego: 650+ rows /
    40+ pages, newest first), and the "More than 150 Record(s)" counter is
    lazy, so we can't trust a total. We stop when: the "Next page" button is
    disabled, a page adds no new rows, `max_pages` is hit, or — once at least
    one wanted row was seen — `stop_after_empty_pages` consecutive pages
    contain no wanted (e.g. Open) rows. Open RFxs cluster on the first pages
    because the grid is sorted newest-first.
  * Each row's edit icon links to a public detail page,
    /page.aspx/en/bpm/process_manage_extranet/<data-id>, which we use as `url`.
  * The listing includes non-open statuses on some tenants; we keep only rows
    whose RFx Status is "Open" or "Released" (NYC PASSPort's word for open;
    configurable via `statuses`).
  * One solicitation can appear as several rows (one per Lot / Round) sharing
    the same RFx #. We emit ONE Opportunity per RFx # (first row seen = lot 1)
    and note the lot count in the description.

Config:
    name: "Ivalua"
    agencies:
      - base_url: "https://sdbuynet.sandiegocounty.gov"
        agency: "San Diego County"
        state: "CA"
    # optional: statuses: ["Open", "Released"]   (empty list = keep every status)
    # optional: max_pages: 50
    # optional: stop_after_empty_pages: 3
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_LIST_PATH = "/page.aspx/en/rfp/request_browse_public"
_DETAIL_PATH = "/page.aspx/en/bpm/process_manage_extranet/{}"
_GRID_ID = "body_x_grid_grd"

# Logical field -> (th id suffixes, header label fallbacks)
_COLUMNS = {
    "code": (("colBpmCodeCalculated", "colCode"), ("rfx #", "rfx number", "solicitation #")),
    "title": (("colLabel",), ("rfx name", "name", "title")),
    "commodity": (("colFamily", "colCommodities", "colFamilies"), ("commodities", "commodity")),
    "begin": (("colBeginDate", "colRfpBeginDate"), ("rfx begin date", "begin date", "start date")),
    "end": (("colEndDate", "colRfpEndDate"), ("rfx end date", "end date", "closing date")),
    "status": (("colStatus", "colRfpStatus"), ("rfx status", "status")),
}


class IvaluaSource(Source):
    """Reads open solicitations from a list of Ivalua tenant portals."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Ivalua")
        self.agencies = cfg.get("agencies", [])
        self.timeout = int(cfg.get("timeout", 30))
        self.max_pages = int(cfg.get("max_pages", 50))
        self.stop_after_empty = int(cfg.get("stop_after_empty_pages", 3))
        statuses = cfg.get("statuses", ["Open", "Released"])
        self.statuses = {s.lower() for s in statuses} if statuses else set()

    def fetch(self) -> list[Opportunity]:
        return self.fetch_each(self.agencies, self._fetch_agency)

    # ------------------------------------------------------------------
    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        base_url = ag["base_url"].rstrip("/")
        host = urlparse(base_url).netloc
        agency_name = ag.get("agency", host)
        state = ag.get("state", "")
        listing_url = base_url + _LIST_PATH

        session = requests.Session()
        session.headers["User-Agent"] = _BROWSER_UA
        resp = session.get(listing_url, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        if soup.find(id=_GRID_ID) is None:
            raise RuntimeError(f"Ivalua grid not found at {listing_url}")

        results: dict[str, Opportunity] = {}
        lots: dict[str, int] = {}
        seen_rows: set[str] = set()
        page = 0
        empty_streak = 0
        any_match = False
        while True:
            new_rows, matched = self._parse_page(soup, base_url, host, listing_url,
                                                 agency_name, state, seen_rows, results, lots)
            page += 1
            any_match = any_match or matched > 0
            empty_streak = 0 if matched else empty_streak + 1
            if (new_rows == 0 or page >= self.max_pages or not self._has_next(soup)
                    or (self.statuses and any_match and empty_streak >= self.stop_after_empty)):
                break
            soup = self._postback_page(session, soup, listing_url, page)

        for code, n in lots.items():
            if n > 1:
                results[code].description += f" ({n} lots)"
        return list(results.values())

    def _parse_page(self, soup, base_url, host, listing_url, agency_name, state,
                    seen_rows, results, lots) -> tuple[int, int]:
        """Parse one grid page. Returns (new rows seen, rows matching statuses)."""
        grid = soup.find(id=_GRID_ID)
        if grid is None:
            raise RuntimeError(f"Ivalua grid missing on a result page of {listing_url}")
        colmap = self._column_map(grid)
        if "title" not in colmap:
            raise RuntimeError(f"Ivalua grid at {listing_url}: no RFx Name column")

        new_rows = matched = 0
        for row in grid.select("tr[data-object-type]"):
            row_id = row.get("data-id", "")
            if not row_id or row_id in seen_rows:
                continue
            seen_rows.add(row_id)
            new_rows += 1
            cells = row.find_all("td", recursive=False)

            def cell(key: str) -> str:
                i = colmap.get(key)
                return clean_text(cells[i].get_text(" ")) if i is not None and i < len(cells) else ""

            status = cell("status")
            if self.statuses and status.lower() not in self.statuses:
                continue
            title = cell("title")
            if not title:
                continue
            code = cell("code") or row_id
            matched += 1
            lots[code] = lots.get(code, 0) + 1
            if code in results:  # another Lot/Round of a solicitation we already have
                continue

            link = row.find("a", href=lambda h: h and "process_manage_extranet" in h)
            url = urljoin(base_url, link["href"]) if link else base_url + _DETAIL_PATH.format(row_id)

            commodity = cell("commodity")
            description = f"{title} — {commodity}" if commodity else title
            results[code] = Opportunity(
                id=f"ivalua:{host}:{code}",
                source_name=f"{self.name} — {agency_name}",
                title=title,
                description=description,
                agency=agency_name,
                state=state,
                url=url,
                psc=commodity,
                posted_date=to_iso_date(cell("begin")),
                due_date=to_iso_date(cell("end")),
            )
        return new_rows, matched

    @staticmethod
    def _column_map(grid) -> dict:
        """Map logical field -> td index, using header ids first, labels second."""
        headers = grid.find("tr").find_all("th")
        colmap: dict[str, int] = {}
        for idx, th in enumerate(headers):
            th_id = th.get("id", "")
            label = clean_text(th.get_text(" ")).lower()
            for key, (id_suffixes, labels) in _COLUMNS.items():
                if key in colmap:
                    continue
                if any(th_id.endswith(sfx) for sfx in id_suffixes) or label in labels:
                    colmap[key] = idx
                    break
        return colmap

    @staticmethod
    def _has_next(soup) -> bool:
        btn = soup.find(id="body_x_grid_gridPagerBtnNextPage")
        if btn is None:
            return False
        return "disabled" not in (btn.get("class") or []) and not btn.has_attr("disabled")

    def _postback_page(self, session, soup, listing_url: str, page_index: int):
        """Do what the grid's "Next page" button does: post the form back."""
        form = soup.find("form", id="mainForm") or soup.find("form")
        data: dict[str, str] = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            itype = (inp.get("type") or "text").lower()
            if itype in ("radio", "checkbox") and not inp.has_attr("checked"):
                continue
            if itype in ("submit", "button", "image", "file"):
                continue
            data[name] = inp.get("value", "")
        for sel in form.find_all("select"):
            name = sel.get("name")
            if name:
                opt = sel.find("option", selected=True) or sel.find("option")
                data[name] = opt.get("value", "") if opt else ""
        data["__EVENTTARGET"] = _GRID_ID  # GridView UniqueID == ClientID here
        data["__EVENTARGUMENT"] = f"Page|{page_index}"

        action = urljoin(listing_url, form.get("action") or listing_url)
        resp = session.post(action, data=data, timeout=self.timeout,
                            headers={"Referer": listing_url})
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
