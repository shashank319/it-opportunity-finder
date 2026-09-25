"""
schenectady_county.py — open bids from Schenectady County, NY.

A one-off adapter: the county publishes its current bids as a Drupal Views
listing at /current-bids — server-rendered, no login, with clean per-bid
fields (bid number, title + document link, posted date, due date, status).

Unlike the CivicPlus adapter, this one is NOT generic: Drupal Views field
classes (views-field-field-bid-no, ...) are configured per site, so this
markup is specific to this county rather than a shared off-the-shelf layout.
"""

from __future__ import annotations

from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

LISTING_URL = "https://www.schenectadycountyny.gov/current-bids"
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class SchenectadyCountySource(Source):
    """Reads open bids from Schenectady County NY's current-bids page."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Schenectady County, NY")
        self.state = cfg.get("state", "NY")
        self.url = cfg.get("url", LISTING_URL)
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        resp = requests.get(self.url, headers={"User-Agent": _BROWSER_UA}, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        results: list[Opportunity] = []
        for item in soup.select("div.item"):
            opp = self._item_to_opp(item)
            if opp:
                results.append(opp)
        return results

    def _item_to_opp(self, item):
        title_a = item.select_one(".views-field-title a")
        if not title_a:
            return None
        title = clean_text(title_a.get_text())
        if not title:
            return None

        status_el = item.select_one(".views-field-field-bid-status")
        status = clean_text(status_el.get_text()) if status_el else ""
        # The page lists closed/reopened bids too — keep only open ones.
        if status and "open" not in status.lower():
            return None

        bid_no_el = item.select_one(".views-field-field-bid-no")
        bid_no = clean_text(bid_no_el.get_text()).replace("Bid #:", "").strip() if bid_no_el else ""

        return Opportunity(
            id=f"schenectadycounty:{bid_no or title}",
            source_name=self.name,
            title=title,
            description=title,
            agency="Schenectady County, NY",
            state=self.state,
            url=urljoin(self.url, title_a.get("href", "")),
            set_aside=bid_no,
            posted_date=to_iso_date(self._time_value(item, ".views-field-field-posted-date")),
            due_date=to_iso_date(self._time_value(item, ".views-field-field-bid-received-no-later-than")),
        )

    def _time_value(self, item, selector: str) -> str:
        """Read a Drupal <time datetime="..."> value, falling back to its text."""
        el = item.select_one(f"{selector} time")
        if not el:
            return ""
        return el.get("datetime") or clean_text(el.get_text())
