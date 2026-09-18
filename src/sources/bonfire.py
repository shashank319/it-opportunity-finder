"""
bonfire.py — open opportunities from Bonfire (bonfirehub.com) agency portals.

Bonfire hosts each agency on its own subdomain (e.g. ventura.bonfirehub.com).
The portal's "Open Opportunities" tab loads from a plain, unauthenticated JSON
endpoint — no login, no session:
    https://<tenant>.bonfirehub.com/PublicPortal/getOpenPublicOpportunitiesSectionData

Config-driven: give it a list of agencies (tenant + name + state). Find the
tenant from the agency's Bonfire URL, e.g. ventura.bonfirehub.com -> tenant
"ventura".
"""

from __future__ import annotations

import requests

from .base import Source
from ..models import Opportunity
from ..util import to_iso_date, clean_text

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class BonfireSource(Source):
    """Reads open opportunities from a list of Bonfire agency portals."""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "Bonfire")
        self.agencies = cfg.get("agencies", [])
        self.timeout = int(cfg.get("timeout", 30))

    def fetch(self) -> list[Opportunity]:
        out: list[Opportunity] = []
        for ag in self.agencies:
            if not ag.get("enabled", True):
                continue
            out.extend(self._fetch_agency(ag))
        return out

    def _fetch_agency(self, ag: dict) -> list[Opportunity]:
        tenant = ag["tenant"]
        agency_name = ag.get("agency", tenant)
        state = ag.get("state", "")
        url = f"https://{tenant}.bonfirehub.com/PublicPortal/getOpenPublicOpportunitiesSectionData"
        resp = requests.get(
            url, headers={"User-Agent": _BROWSER_UA, "Accept": "application/json"}, timeout=self.timeout
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Bonfire: unexpected response for tenant '{tenant}'")
        projects = (data.get("payload") or {}).get("projects") or {}

        results: list[Opportunity] = []
        for p in projects.values():
            opp = self._project_to_opp(p, tenant, agency_name, state)
            if opp:
                results.append(opp)
        return results

    def _project_to_opp(self, p: dict, tenant: str, agency_name: str, state: str):
        title = clean_text(p.get("ProjectName"))
        if not title:
            return None
        pid = p.get("ProjectID")
        return Opportunity(
            id=f"bonfire:{tenant}:{pid}",
            source_name=f"{self.name} — {agency_name}",
            title=title,
            description=title,
            agency=agency_name,
            state=state,
            url=f"https://{tenant}.bonfirehub.com/opportunities/{pid}",
            set_aside=clean_text(p.get("ReferenceID")),
            due_date=to_iso_date(p.get("DateClose")),
        )
