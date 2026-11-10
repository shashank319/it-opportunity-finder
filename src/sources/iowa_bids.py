"""
iowa_bids.py — open bid opportunities from the State of Iowa.

bidopportunities.iowa.gov (Iowa DAS, no login) lists every open state-agency
bid in a DataTables grid that the page fills from a JSON endpoint:

    GET /Home/DT_HostedBidsSearch?agencyId=&enteredSearchText=

The endpoint returns all open bids in one response (aaData), with bid
number, agency, title, full description and .NET "/Date(ms)/" timestamps.
Each bid links to the public /Home/BidInfo?bidId=<guid> page.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    _CENTRAL = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover
    _CENTRAL = timezone.utc

import requests

from .base import Source
from ..models import Opportunity
from ..util import clean_text

BASE_URL = "https://bidopportunities.iowa.gov"
DATA_URL = f"{BASE_URL}/Home/DT_HostedBidsSearch?agencyId=&enteredSearchText="
_NET_DATE = re.compile(r"/Date\((-?\d+)")

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def _net_date(value) -> str:
    """'/Date(1794510000000)/' -> '2026-11-12' (Iowa local date)."""
    m = _NET_DATE.search(str(value or ""))
    if not m:
        return ""
    return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).astimezone(_CENTRAL).date().isoformat()


class IowaBidsSource(Source):
    """Reads open bids from bidopportunities.iowa.gov."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "State of Iowa — Bid Opportunities")
        self.state = cfg.get("state", "IA")
        self.url = cfg.get("url", DATA_URL)
        self.timeout = int(cfg.get("timeout", 60))

    def fetch(self) -> list[Opportunity]:
        resp = requests.get(
            self.url,
            headers={"User-Agent": _BROWSER_UA, "X-Requested-With": "XMLHttpRequest", "Referer": BASE_URL + "/"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        rows = resp.json().get("aaData")
        if rows is None:
            raise RuntimeError("Iowa bids response missing aaData")
        return [o for o in (self._row_to_opp(r) for r in rows) if o]

    def _row_to_opp(self, row: dict):
        title = clean_text(row.get("Solicitation"))
        if not title:
            return None
        if str(row.get("Status") or "Open").lower() != "open":
            return None
        number = clean_text(row.get("BidNumber"))
        bid_id = row.get("ID") or ""
        return Opportunity(
            id=f"iowa:{bid_id or number}",
            source_name=self.name,
            title=f"{number} — {title}" if number else title,
            description=clean_text(row.get("Description")) or title,
            agency=f"State of Iowa — {clean_text(row.get('AgencyName'))}".rstrip(" —"),
            state=self.state,
            url=f"{BASE_URL}/Home/BidInfo?bidId={bid_id}" if bid_id else BASE_URL,
            posted_date=_net_date(row.get("EffectiveDate") or row.get("CreatedDate")),
            due_date=_net_date(row.get("ExpirationDate")),
        )
