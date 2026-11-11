"""
procureware.py — open bids from ProcureWare (Contruent) agency portals.

ProcureWare hosts a per-agency subdomain portal (e.g. hennepin.procureware.com).
The public /Bids page is an empty shell; its Kendo grid fetches rows from
POST /domain/Generic/Grid (form-encoded, the same request the page's own JS
sends for an anonymous visitor). The JSON reply's "Message" field is itself a
JSON string holding {"Data": [...rows...], "UniqueCount": N}.

The public grid lists ALL bids (open, closed, awarded, cancelled), so we page
through it and keep only open ones. Status codes: 3 = Open for Bidding,
4 = Closed, 1 = Awarded, 2 = Cancelled, 0 = Draft. Agencies can also set a
custom status label (returned as a string, e.g. "Not Awarded"); for those we
keep the row only if its due date is still in the future and the label doesn't
look closed/awarded/cancelled.

Config-driven: give it a list of agencies (tenant + name + state). Find the
tenant from the agency's ProcureWare URL, e.g. hennepin.procureware.com ->
tenant "hennepin".
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone

import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_OPEN_STATUS = 3
_PAGE_SIZE = 200
_MAX_PAGES = 25  # safety cap: 5,000 rows
_CLOSED_LABEL = re.compile(r"clos|award|cancel|complet|reject|no bid|negotiat|evaluat", re.I)


def _text(value) -> str:
    """clean_text plus HTML-entity decoding (grid values arrive as '&amp;')."""
    return clean_text(html.unescape(str(value))) if value else ""


class ProcureWareSource(Source):
    """Reads open bids from a list of ProcureWare agency portals."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "ProcureWare")
        self.agencies = cfg.get("agencies", [])
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        return self.fetch_each(self.agencies, self._fetch_agency)

    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        tenant = ag["tenant"]
        agency_name = ag.get("agency", tenant)
        state = ag.get("state", "")
        base = f"https://{tenant}.procureware.com"

        session = requests.Session()
        session.headers["User-Agent"] = _BROWSER_UA
        # Load the public page first so the session has the site's cookies.
        session.get(f"{base}/Bids", timeout=self.timeout).raise_for_status()

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        results: list[Opportunity] = []
        seen = 0
        for page in range(1, _MAX_PAGES + 1):
            rows, total = self._read_page(session, base, page)
            seen += len(rows)
            for row in rows:
                if not self._is_open(row, now):
                    continue
                opp = self._row_to_opp(row, tenant, base, agency_name, state)
                if opp:
                    results.append(opp)
            if not rows or seen >= total:
                break
        return results

    def _read_page(self, session: requests.Session, base: str, page: int):
        data = {
            "view": "bid_layout_first_view",
            "grid": "bid_grid",
            "CurrMenu": "Bids",
            "settings[Page]": str(page),
            "settings[PageSize]": str(_PAGE_SIZE),
        }
        resp = session.post(
            f"{base}/domain/Generic/Grid",
            data=data,
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{base}/Bids"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        outer = resp.json()
        if outer.get("StatusCode") != 1:
            raise RuntimeError(f"ProcureWare grid error: {str(outer.get('Message'))[:200]}")
        inner = json.loads(outer["Message"])
        return inner.get("Data") or [], int(inner.get("UniqueCount") or 0)

    @staticmethod
    def _is_open(row: dict, now_iso: str) -> bool:
        status = row.get("FullBidView_CalculatedAdjustedStatus")
        if status == _OPEN_STATUS:
            return True
        if isinstance(status, str):
            due = row.get("FullBidView_DueDate") or ""
            return bool(due) and due > now_iso and not _CLOSED_LABEL.search(status)
        return False

    def _row_to_opp(self, row: dict, tenant: str, base: str, agency_name: str, state: str):
        title = _text(row.get("FullBidView_Title"))
        if not title:
            return None
        number = _text(row.get("FullBidView_Number"))
        guid = row.get("FullBidView_GuidId") or ""
        bid_type = _text(row.get("FullBidView_BidTypeName"))
        categories = _text(row.get("FullBidView_CategorySummary"))
        desc = " | ".join(p for p in (title, bid_type, categories and f"Categories: {categories}") if p)
        return Opportunity(
            id=f"procureware:{tenant}:{number or guid or title}",
            source_name=f"{self.name} — {agency_name}",
            title=title,
            description=desc,
            agency=agency_name,
            state=state,
            url=f"{base}/Bids/{guid}" if guid else f"{base}/Bids",
            set_aside=number,
            posted_date=to_iso_date(row.get("FullBidView_AvailableDate")),
            due_date=to_iso_date(row.get("FullBidView_DueDate")),
        )
