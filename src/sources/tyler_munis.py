"""
tyler_munis.py — open bids from Tyler Technologies "Munis Self Service"
vendor portals (<tenant>.munisselfservice.com).

The public Bid Search page (/Vendors/VBids/Default.aspx) needs no login, but
it is an ASP.NET WebForms page with two quirks a plain GET trips over:
  * The first request only sets cookies and 302s to /common/Upgrade.aspx
    ("Upgrade Browser" / "cookies are turned off"). Re-requesting with the
    cookies the server just set (a requests.Session) gets the real page.
  * The results grid is not in the initial HTML — you have to post the
    search form back (with its __VIEWSTATE etc.) with "Open Bids only"
    ticked. Results come back 10 per page; further pages are __doPostBack
    events on the grid, which we replay the same way.
No credentials, no JS — just the same form a visitor submits.

Config-driven: give it a list of agencies (base_url + name + state), e.g.
base_url "https://stlouiscountymovendors.munisselfservice.com". If a tenant
serves the vendor site somewhere other than /Vendors, set `vendors_path`.

NOTE: the per-bid detail view (Detail.aspx) is session-bound — it has no
stable URL — so every opportunity links to the tenant's public Bid Search
page; find the bid there by its number (kept in `set_aside`, as ionwave.py
does, and in the id). The tenant's RSS feed (BidsRssFeed.ashx) would be
nicer but returned HTTP 500 when tested, so we don't rely on it.
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
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Prefix shared by every control on the Bid Search / Search Results pages.
_P = "ctl00$ctl00$PrimaryPlaceHolder$ContentPlaceHolderMain$"
_GRID = _P + "MolGridView1"
# Grid pager links look like __doPostBack('...MolGridView1','page:11-20:1').
_PAGE_ARG = re.compile(r"__doPostBack\('[^']*MolGridView1','(page:[^']+)'\)")
_CLOSED_STATUSES = {"closed", "awarding", "awarded", "cancelled", "canceled", "rejected"}
_MAX_PAGES = 50  # safety cap (10 rows/page) so a bad filter can't loop forever


def _hidden_fields(soup) -> dict:
    return {i["name"]: i.get("value", "") for i in soup.select("input[type=hidden]") if i.get("name")}


class TylerMunisSource(Source):
    """Reads open bids from a list of Tyler Munis Self Service vendor portals."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Tyler Munis Self Service")
        self.agencies = cfg.get("agencies", [])
        self.timeout = int(cfg.get("timeout", 30))
        # open_only=False is only useful for debugging the parser on a tenant
        # that currently has no open bids.
        self.open_only = bool(cfg.get("open_only", True))

    def fetch(self) -> list[Opportunity]:
        return self.fetch_each(self.agencies, self._fetch_agency)

    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        base = ag["base_url"].rstrip("/")
        vendors = "/" + ag.get("vendors_path", "Vendors").strip("/")
        agency_name = ag.get("agency", base)
        state = ag.get("state", "")
        search_url = f"{base}{vendors}/VBids/Default.aspx"
        public_url = f"{base}{vendors}/VBids/BidVersionSelector.aspx"

        s = requests.Session()
        s.headers["User-Agent"] = _BROWSER_UA
        # Cookie handshake: the first hit only sets cookies (and 302s to the
        # "Upgrade Browser" page); the second, with cookies, gets real content.
        s.get(f"{base}{vendors}/Default.aspx", timeout=self.timeout)
        resp = s.get(search_url, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        if not soup.find("input", attrs={"name": _P + "Search"}):
            raise RuntimeError(f"Munis bid search form not found at {resp.url} (title: "
                               f"{clean_text(soup.title.get_text()) if soup.title else '?'})")

        form = _hidden_fields(soup)
        form[_P + "BidTypeDropBox"] = "0"  # any type
        form[_P + "Search"] = "Search"
        if self.open_only:
            form[_P + "OpenBidsOnly"] = "on"
        resp = s.post(resp.url, data=form, timeout=self.timeout)
        resp.raise_for_status()

        results: list[Opportunity] = []
        seen_pages: set[str] = set()
        for _ in range(_MAX_PAGES):
            soup = BeautifulSoup(resp.text, "html.parser")
            grid = soup.find(id=lambda x: x and x.endswith("MolGridView1"))
            if grid is None:
                # "No Bid records were found" — a normal, empty result.
                if "No Bid records were found" in resp.text:
                    break
                raise RuntimeError(f"Munis results grid not found at {resp.url}")
            for tr in grid.find_all("tr"):
                opp = self._row_to_opp(tr, base, public_url, agency_name, state)
                if opp:
                    results.append(opp)

            # Follow the pager: the first page arg we haven't visited yet that
            # comes after the current one. Pager args are 'page:<range>:<idx>'.
            next_arg = None
            for arg in _PAGE_ARG.findall(resp.text.replace("&#39;", "'")):
                if arg not in seen_pages:
                    next_arg = arg
                    break
            if not next_arg:
                break
            seen_pages.add(next_arg)
            form = _hidden_fields(soup)
            form["__EVENTTARGET"] = _GRID
            form["__EVENTARGUMENT"] = next_arg
            resp = s.post(resp.url, data=form, timeout=self.timeout)
            resp.raise_for_status()

        # The pager can re-show a page we've already parsed; dedupe by id.
        unique = {o.id: o for o in results}
        return list(unique.values())

    def _row_to_opp(self, tr, base: str, public_url: str, agency_name: str, state: str):
        cells = tr.find_all("td")
        # Columns: [0] Type, [1] Number (postback link), [2] Description,
        # [3] Due By, [4] Opening, [5] Status. Pager rows have fewer cells.
        if len(cells) < 6:
            return None
        bid_type = clean_text(cells[0].get_text())
        bid_number = clean_text(cells[1].get_text())
        title = clean_text(cells[2].get_text())
        status = clean_text(cells[5].get_text())
        if not title or not bid_number:
            return None
        # The server already filtered to open bids; this is just a backstop
        # against a tenant whose "open only" filter misbehaves.
        if self.open_only and status.lower() in _CLOSED_STATUSES:
            return None
        host = re.sub(r"^https?://", "", base).split(".")[0]
        desc = " — ".join(x for x in (bid_type, title, f"Status: {status}" if status else "") if x)
        return Opportunity(
            id=f"munis:{host}:{bid_number}",
            source_name=f"{self.name} — {agency_name}",
            title=title,
            description=desc,
            agency=agency_name,
            state=state,
            url=public_url,
            set_aside=bid_number,
            due_date=to_iso_date(clean_text(cells[3].get_text())),
        )
