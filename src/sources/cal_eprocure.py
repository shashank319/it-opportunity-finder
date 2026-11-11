"""
cal_eprocure.py — posted events from California's Cal eProcure portal.

The public event search (caleprocure.ca.gov/pages/Events-BS3/event-search.aspx,
no login) is a skin over a PeopleSoft Strategic Sourcing guest page. That
underlying page is served server-rendered, with every currently *Posted*
event already in its grid, at:

    /psc/psfpd1/SUPPLIER/ERP/c/AUC_MANAGE_BIDS.AUC_RESP_INQ_AUC.GBL

so a plain cookie-keeping GET is enough. Fields are read by PeopleSoft's
stable field ids (RESP_INQA1_WK_ZZ_AUC_NAME$<row>, ...).

Each event links to Cal eProcure's public friendly URL
/event/<business unit>/<event id>, which redirects to the event-details page.

CAVEAT: the grid gives only the department's numeric Business Unit (e.g.
2740 = DMV), not its name, so `agency` reads "State of California (BU 2740, dmv.ca.gov)" — the
buyer's email domain is added as a readable hint.
"""

from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

BASE_URL = "https://caleprocure.ca.gov"
DATA_URL = (
    f"{BASE_URL}/psc/psfpd1/SUPPLIER/ERP/c/AUC_MANAGE_BIDS.AUC_RESP_INQ_AUC.GBL"
    "?Page=AUC_RESP_INQ_AUC&Action=U"
)
LISTING_URL = f"{BASE_URL}/pages/Events-BS3/event-search.aspx"
_TZ_SUFFIX = re.compile(r"\s*[A-Z]{2,4}$")

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class CalEProcureSource(Source):
    """Reads posted events from California's Cal eProcure."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "California Cal eProcure")
        self.state = cfg.get("state", "CA")
        self.url = cfg.get("url", DATA_URL)
        self.timeout = int(cfg.get("timeout", 90))

    def fetch(self) -> list[Opportunity]:
        s = requests.Session()
        s.headers["User-Agent"] = _BROWSER_UA
        resp = s.get(self.url, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        ids = soup.select("a[id^='AUC_ID_COL$']")
        if not ids and "RESP_INQA" not in resp.text:
            raise RuntimeError("Cal eProcure event grid not found (page changed?)")

        results: list[Opportunity] = []
        for a in ids:
            row = a["id"].split("$", 1)[1]
            opp = self._row_to_opp(soup, row, clean_text(a.get_text()))
            if opp:
                results.append(opp)
        return results

    def _field(self, soup, name: str, row: str) -> str:
        el = soup.find(id=f"{name}${row}")
        return clean_text(el.get_text(" ")) if el else ""

    @staticmethod
    def _agency(bu: str, email: str) -> str:
        """Business Unit code, plus the buyer's email domain as a readable hint
        (e.g. 'State of California (BU 2740, dmv.ca.gov)')."""
        domain = email.rsplit("@", 1)[1].lower() if "@" in email else ""
        hint = ", ".join(x for x in (f"BU {bu}" if bu else "", domain) if x)
        return f"State of California ({hint})" if hint else "State of California"

    def _row_to_opp(self, soup, row: str, event_id: str):
        title = self._field(soup, "RESP_INQA1_WK_ZZ_AUC_NAME", row)
        if not title or not event_id:
            return None
        status = self._field(soup, "ZZ_DERIVED_DESCR254", row)
        if status and status.lower() != "posted":
            return None
        bu = self._field(soup, "RESP_INQA1_WK_BUSINESS_UNIT", row)
        due = _TZ_SUFFIX.sub("", self._field(soup, "RESP_INQA1_WK_AUC_DTTM_FINISH", row).replace("\xa0", " "))
        ev_type = self._field(soup, "RESP_INQA1_WK_AUC_TYPE", row)
        contact = self._field(soup, "RESP_INQA1_WK_OPRDEFNDESC", row)
        email = self._field(soup, "RESP_INQA1_WK_EMAILID", row)
        desc_bits = [title]
        if ev_type:
            desc_bits.append(f"Type: {ev_type}")
        if contact or email:
            desc_bits.append(f"Contact: {contact} {email}".strip())
        return Opportunity(
            id=f"caleprocure:{bu}:{event_id}",
            source_name=self.name,
            title=title if title.startswith(event_id) else f"{event_id} — {title}",
            description=" | ".join(desc_bits),
            agency=self._agency(bu, email),
            state=self.state,
            url=f"{BASE_URL}/event/{bu}/{event_id}" if bu else LISTING_URL,
            due_date=to_iso_date(due),
        )
