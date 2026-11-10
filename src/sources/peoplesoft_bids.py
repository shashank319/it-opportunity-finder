"""
peoplesoft_bids.py — open sourcing events from Oracle PeopleSoft Strategic
Sourcing public "Bidding Opportunities" pages.

Several states run PeopleSoft FSCM for procurement and expose a guest
(no-login) Fluid page listing open bid events:

    .../psc/<site>/SUPPLIER/ERP/c/SCP_PUBLIC_MENU_FL.SCP_PUB_BID_CMP_FL.GBL

e.g. Oklahoma OMES:
    https://financials.ok.gov/psc/SOKLFP1DS/SUPPLIER/ERP/c/SCP_PUBLIC_MENU_FL.SCP_PUB_BID_CMP_FL.GBL

The grid ("Bidding Event Information") is server-rendered in the initial
HTML: Event Name, Business Unit (issuing agency), Event ID, Event Format,
Event Type, Ends In, Start Date, End Date. A plain GET with a cookie-keeping
session returns it — no login, no JS. (PeopleSoft sets session cookies on
the first response and may bounce through a redirect, so we use a
requests.Session.)

Config-driven: give it a list of portals (url + name + state). Fields are
read by PeopleSoft's stable field ids (SCP_PUB_AUC_VW_AUC_NAME$<row> etc.)
so the same parser serves any tenant using the delivered page.

NOTE: the per-event "Details" chevron is a PeopleSoft postback with no
stable URL, so every opportunity links back to the public list page; find
the event there by its Event ID (kept in `set_aside`, as ionwave.py does).
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

_NAME_ID = re.compile(r"^SCP_PUB_AUC_VW_AUC_NAME\$(\d+)$")
# PeopleSoft dates look like "11/17/2026 03:00 PM CST"; drop the zone
# abbreviation so dateutil doesn't choke/warn on it.
_TZ = re.compile(r"\s+[A-Z]{2,4}$")


class PeopleSoftBidsSource(Source):
    """Reads open bid events from PeopleSoft public Bidding Opportunities pages."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "PeopleSoft Bids")
        self.portals = cfg.get("portals", [])
        self.timeout = int(cfg.get("timeout", 40))

    def fetch(self) -> list[Opportunity]:
        return self.fetch_each(self.portals, self._fetch_portal)

    def _fetch_portal(self, p: dict) -> list[Opportunity]:
        url = p["url"]
        label = p.get("agency", url)
        state = p.get("state", "")
        s = requests.Session()
        s.headers["User-Agent"] = _BROWSER_UA
        resp = s.get(url, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        if "Bidding Event Information" not in resp.text:
            raise RuntimeError(f"PeopleSoft bid events grid not found at {resp.url} "
                               f"(title: {clean_text(soup.title.get_text()) if soup.title else '?'})")

        def field(prefix: str, row: str) -> str:
            el = soup.find(id=f"{prefix}${row}")
            return clean_text(el.get_text()) if el else ""

        results: list[Opportunity] = []
        for span in soup.find_all("span", id=_NAME_ID):
            row = _NAME_ID.match(span["id"]).group(1)
            title = clean_text(span.get_text())
            event_id = field("SCP_PUB_AUC_VW_AUC_ID", row)
            if not title or not event_id:
                continue
            bu = field("BUS_UNIT_AUC_VW_DESCR", row)
            ev_type = field("SCP_PUB_AUC_VW_AUC_TYPE", row)
            start = _TZ.sub("", field("SCP_COSP_WK_FL_SCP_STRT_DATE_CHAR", row))
            end = _TZ.sub("", field("SCP_COSP_WK_FL_SCP_END_DATE_CHAR", row))
            host = re.sub(r"^https?://", "", url).split("/")[0]
            results.append(Opportunity(
                id=f"peoplesoft:{host}:{event_id}",
                source_name=f"{self.name} — {label}",
                title=title,
                description=" — ".join(x for x in (title, bu, f"{ev_type} {event_id}".strip()) if x),
                agency=bu or label,
                state=state,
                url=url,
                set_aside=event_id,
                posted_date=to_iso_date(start),
                due_date=to_iso_date(end),
            ))
        return results
