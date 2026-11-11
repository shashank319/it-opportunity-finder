"""
beaconbid.py — open solicitations from BeaconBid agency pages.

BeaconBid (www.beaconbid.com) is a single-page app; each agency's public page
lives at /solicitations/<agency-tag>/open. The page loads its rows from the
site's GraphQL endpoint (POST /api/gql?operation=ListSolicitations) with
status "open" — the same anonymous request the browser makes, no login.

Dates come back as {"utcDate": ..., "specifiedZone": "America/Chicago"}; we
convert to the agency's own time zone before taking the calendar date so a
5 PM Central deadline doesn't roll over to the next day in UTC.

Config-driven: give it a list of agencies (tag + name + state). The tag is
the path segment of the agency's BeaconBid URL, e.g.
beaconbid.com/solicitations/greene-county-mo/open -> tag "greene-county-mo".
"""

from __future__ import annotations

from datetime import datetime

import requests

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_BASE = "https://www.beaconbid.com"
_PAGE_SIZE = 100
_MAX_PAGES = 10

_QUERY = """
query ListSolicitations($agencyTag: String, $status: String, $start: Int, $pageSize: Int) {
  solicitations(agencyTag: $agencyTag, status: $status, start: $start, pageSize: $pageSize) {
    total
    data {
      id refnum title type issueDate dueDate departments categories description
      agency { tag name location { region { abbr } } }
    }
  }
}
"""


def _local_date(value) -> str:
    """BeaconBid date object -> 'YYYY-MM-DD' in the agency's own time zone."""
    if not isinstance(value, dict):
        return to_iso_date(value)
    utc = value.get("utcDate") or ""
    zone = value.get("specifiedZone")
    if utc and zone and ZoneInfo is not None:
        try:
            dt = datetime.fromisoformat(utc.replace("Z", "+00:00"))
            return dt.astimezone(ZoneInfo(zone)).date().isoformat()
        except Exception:
            pass
    return to_iso_date(utc)


class BeaconBidSource(Source):
    """Reads open solicitations from a list of BeaconBid agency pages."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "BeaconBid")
        self.agencies = cfg.get("agencies", [])
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        return self.fetch_each(self.agencies, self._fetch_agency)

    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        tag = ag["tag"]
        results: list[Opportunity] = []
        start = 0
        for _ in range(_MAX_PAGES):
            resp = requests.post(
                f"{_BASE}/api/gql?operation=ListSolicitations",
                json={
                    "operationName": "ListSolicitations",
                    "query": _QUERY,
                    "variables": {"agencyTag": tag, "status": "open", "start": start, "pageSize": _PAGE_SIZE},
                },
                headers={
                    "User-Agent": _BROWSER_UA,
                    "Referer": f"{_BASE}/solicitations/{tag}/open",
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("errors"):
                raise RuntimeError(f"BeaconBid GraphQL error: {body['errors'][0].get('message')}")
            block = (body.get("data") or {}).get("solicitations") or {}
            rows = block.get("data") or []
            for row in rows:
                opp = self._row_to_opp(row, tag, ag)
                if opp:
                    results.append(opp)
            start += len(rows)
            if not rows or start >= int(block.get("total") or 0):
                break
        return results

    def _row_to_opp(self, row: dict, tag: str, ag: dict):
        title = clean_text(row.get("title"))
        if not title:
            return None
        agency_info = row.get("agency") or {}
        agency_name = ag.get("agency") or agency_info.get("name") or tag
        region = ((agency_info.get("location") or {}).get("region") or {}).get("abbr") or ""
        refnum = clean_text(row.get("refnum"))
        sol_id = row.get("id") or ""
        desc_html = (row.get("description") or {}).get("html") if isinstance(row.get("description"), dict) else row.get("description")
        departments = ", ".join(row.get("departments") or [])
        description = " | ".join(
            p for p in (clean_text(desc_html), row.get("type") or "", departments and f"Dept: {departments}") if p
        ) or title
        return Opportunity(
            id=f"beaconbid:{tag}:{refnum or sol_id}",
            source_name=f"{self.name} — {agency_name}",
            title=title,
            description=description,
            agency=agency_name,
            state=ag.get("state") or region,
            url=f"{_BASE}/solicitations/{tag}/{sol_id}" if sol_id else f"{_BASE}/solicitations/{tag}/open",
            set_aside=refnum,
            posted_date=_local_date(row.get("issueDate")),
            due_date=_local_date(row.get("dueDate")),
        )
