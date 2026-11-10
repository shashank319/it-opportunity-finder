"""
infor_sourcing.py — open bids from Infor (CloudSuite / Landmark) supplier portals.

Infor CloudSuite Supply Management (FSM) and the older Lawson Landmark (LM)
procurement suite both publish open sourcing events on an anonymous supplier
portal, e.g.
    https://sms-idaho-prd.tam.inforgov.com/fsm/SupplyManagementSupplier/list/SourcingEvent.OpenForBid?csk.SupplierGroup=LUMA

How the page works (and what we do):
  1. A GET of the list URL bounces through /sso/SSOServlet?_action=LOGINASSERT,
     which simply issues an ANONYMOUS session cookie and redirects back (the
     page's own context says actorId "Anonymous", allowAnonymousAccess true).
     No login form, no credentials.
  2. The Angular client then fetches the rows as JSON from the SAME list URL
     with `pageop=load&pagesize=N` added. We do exactly that with a
     requests.Session that kept the anonymous cookie.

Each row's `fields` dict holds SourcingEvent (event #), Name, OpenDate,
CloseDate (format YYYYMMDDHHMMSSxx), SourcingEventType, and sometimes a
Category. Some tenants (e.g. Coral Gables) mark those fields "isSecured" for
anonymous users and send them blank; we then fall back to the row's
`representativeText` ("172-3 - Fitness Instructors") and the event # in its
resourceId, and leave the dates empty.

Config-driven: each agency entry gives the full public list `url` (copy it from
the browser, including csk.* params) + agency + state. Use the "open for bid"
list (e.g. SourcingEvent.OpenForBid / XiOpenForBid), not "AllEvents", which
returns years of awarded history; rows whose status says Awarded/Closed/etc.
are dropped as a safety net.

NOTE: rows have no stable public detail URL (the portal navigates client-side),
so each opportunity links back to the agency's list page.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_PAGE_SIZE = 500
_CLOSED_STATUSES = {"awarded", "closed", "cancelled", "canceled", "complete", "completed"}
_LAST_NUMBER = re.compile(r"(\d+)\)\s*$")          # SourcingEvent[ByCompany](1,172) -> 172
_REP_PREFIX = re.compile(r"^\s*(\d+(?:-\d+)?)\s+-\s+(.*)$")  # "172-3 - Title"
_STATUS_KEY = "_op_DerivedStatusForSupplier_spc_translation_cp_"


def _val(fields: dict, key: str):
    v = fields.get(key)
    if isinstance(v, dict):
        return v.get("value")
    return None


def _infor_date(value) -> str:
    """'2026111319000000' -> '2026-11-13'."""
    text = str(value or "").strip()
    if len(text) >= 8 and text[:8].isdigit():
        return to_iso_date(f"{text[0:4]}-{text[4:6]}-{text[6:8]}")
    return to_iso_date(text)


class InforSourcingSource(Source):
    """Reads open sourcing events from a list of Infor supplier portals."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Infor Supplier Portal")
        self.agencies = cfg.get("agencies", [])
        self.timeout = int(cfg.get("timeout", 45))

    def fetch(self) -> list[Opportunity]:
        return self.fetch_each(self.agencies, self._fetch_agency)

    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        listing_url = ag["url"]
        agency_name = ag.get("agency", "")
        state = ag.get("state", "")
        host = (urlsplit(listing_url).hostname or "").lower()

        session = requests.Session()
        session.headers["User-Agent"] = _BROWSER_UA
        # Step 1: load the page to pick up the anonymous SSO session cookie.
        page = session.get(listing_url, timeout=self.timeout)
        page.raise_for_status()
        # The anonymous context is marked "Anonymous" (FSM: actorId/isAnonymousUser;
        # older LM: anonymousId). If it's missing we landed on a sign-in page.
        if "/sso/" in page.url or "Anonymous" not in page.text:
            raise RuntimeError(f"no anonymous access (sign-in required?): {page.url}")

        # Step 2: the JSON data call the browser makes.
        sep = "&" if "?" in listing_url else "?"
        data_url = f"{listing_url}{sep}pageop=load&pagesize={_PAGE_SIZE}"
        resp = session.get(data_url, headers={"Accept": "application/json"}, timeout=self.timeout)
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError as e:
            raise RuntimeError(f"list data was not JSON (got {resp.headers.get('content-type')})") from e
        rows = (payload.get("dataViewSet") or {}).get("data")
        if rows is None:
            raise RuntimeError(f"no dataViewSet.data in response (status={payload.get('status')})")

        results: list[Opportunity] = []
        for row in rows:
            opp = self._row_to_opp(row, host, listing_url, agency_name, state)
            if opp:
                results.append(opp)
        return results

    def _row_to_opp(self, row: dict, host, listing_url, agency_name, state):
        f = row.get("fields") or {}
        status = clean_text(_val(f, _STATUS_KEY) or "")
        if status.lower() in _CLOSED_STATUSES:
            return None

        rep = clean_text(row.get("representativeText") or "")
        rep_m = _REP_PREFIX.match(rep)
        title = clean_text(_val(f, "Name") or _val(f, "_op_Name_spc_translation_cp_") or "")
        if not title:
            title = rep_m.group(2) if rep_m else rep
        if not title:
            return None

        event_no = _val(f, "SourcingEvent")
        if not event_no:
            m = _LAST_NUMBER.search(str(row.get("resourceId") or ""))
            event_no = m.group(1) if m else ""
        event_no = str(event_no or "")
        version = clean_text(_val(f, "_op_EventVersionString_spc_translation_cp_") or "")
        if not version and rep_m:
            version = rep_m.group(1)
        ref = clean_text(_val(f, "EventReference") or "")
        ev_type = clean_text(_val(f, "SourcingEventType") or "")
        category = clean_text(_val(f, "Category") or "")

        desc_bits = [b for b in (ev_type, category, f"Event {version or event_no}" if (version or event_no) else "", ref) if b]
        return Opportunity(
            id=f"infor:{host}:{event_no or title}",
            source_name=f"{self.name} — {agency_name}" if agency_name else self.name,
            title=title,
            description=f"{title} ({' | '.join(desc_bits)})" if desc_bits else title,
            agency=agency_name,
            state=state,
            url=listing_url,
            set_aside=ref or version or event_no,
            posted_date=_infor_date(_val(f, "OpenDate")),
            due_date=_infor_date(_val(f, "DerivedCloseDate") or _val(f, "CloseDate")),
        )
