"""
questcdn.py — open postings from QuestCDN agency portals.

QuestCDN's browse page is a jQuery DataTables grid backed by a public,
unauthenticated AJAX endpoint (discovered by watching the portal page's own
network calls):
    https://qcpi.questcdn.com/cdn/browse_posting/?group=<id>&provider=<id>&...

Config-driven: give it a list of agencies (group + provider ids, name,
state). Find both ids from the agency's QuestCDN URL query string, e.g.
qcpi.questcdn.com/cdn/posting/?group=7073&provider=7073 -> group 7073,
provider 7073 (the two are usually equal, one per agency).

NOTE: postings have no plain per-row detail URL (opened via a JS click
handler), so every opportunity links back to the agency's listing page —
find the specific one there by its Project ID (shown in the title).
"""

from __future__ import annotations

import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

API_URL = "https://qcpi.questcdn.com/cdn/browse_posting/"
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Minimal DataTables column spec the endpoint expects — only the fields we
# actually read need to be searchable/orderable; the rest can be blank.
_COLUMNS = [
    "render_my_posting", "render_post_date", "render_project_id",
    "render_category_search_string", "render_name", "bid_date_str",
    "render_city", "render_county", "state_code", "render_owner",
    "render_solicitor", "posting_type", "render_empty", "render_empty",
    "render_empty", "render_empty", "project_id",
]


def _column_params() -> dict:
    params = {}
    for i, col in enumerate(_COLUMNS):
        params[f"columns[{i}][data]"] = col
        params[f"columns[{i}][searchable]"] = "true"
        params[f"columns[{i}][orderable]"] = "true"
    return params


class QuestCdnSource(Source):
    """Reads open postings from a list of QuestCDN agency portals."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "QuestCDN")
        self.agencies = cfg.get("agencies", [])
        self.max_rows = int(cfg.get("max_rows", 100))
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        out: list[Opportunity] = []
        for ag in self.agencies:
            if not ag.get("enabled", True):
                continue
            out.extend(self._fetch_agency(ag))
        return out

    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        group = ag["group"]
        provider = ag.get("provider", group)
        agency_name = ag.get("agency", str(group))
        state = ag.get("state", "")
        listing_url = f"https://qcpi.questcdn.com/cdn/posting/?group={group}&provider={provider}&yr=1"

        params = {"group": group, "provider": provider, "draw": 1, "start": 0, "length": self.max_rows}
        params.update(_column_params())
        resp = requests.get(
            API_URL, params=params,
            headers={"User-Agent": _BROWSER_UA, "Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        rows = resp.json().get("data", []) or []

        results: list[Opportunity] = []
        for row in rows:
            opp = self._row_to_opp(row, listing_url, agency_name, state)
            if opp:
                results.append(opp)
        return results

    def _row_to_opp(self, row: dict, listing_url: str, agency_name: str, state: str):
        title = clean_text(row.get("render_name"))
        if not title:
            return None
        project_id = row.get("project_id") or ""
        owner = clean_text(row.get("render_owner")) or agency_name
        return Opportunity(
            id=f"questcdn:{project_id or title}",
            source_name=f"{self.name} — {agency_name}",
            title=title,
            description=title,
            agency=owner,
            state=state,
            url=listing_url,
            set_aside=str(project_id),
            posted_date=to_iso_date(row.get("render_post_date")),
            due_date=to_iso_date(row.get("bid_date_str")),
        )
